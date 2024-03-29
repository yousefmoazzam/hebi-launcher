import os
import sys
import jwt
import json
import yaml
import signal
import logging
import pickle
from logging.handlers import RotatingFileHandler
from datetime import datetime
from threading import Lock

from kubernetes import config, client, watch
from kubernetes.client.rest import ApiException
from flask import Flask, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from jinja2 import Environment, FileSystemLoader
from ldap3 import Server, Connection, ALL


app = Flask(__name__)
socketio = SocketIO(app)
CORS(app, support_credentials=True)

# dict for tracking the timestamps of the last sign of activity for Hebi
# sessions
all_sessions_activity = {}
# for being careful about the handling of the global dict all_sessions_activity
# which is read/modified by the two socketio background tasks:
# - check_all_sessions_activity()
# - check_for_inactive_sessions()
thread_lock = Lock()

# if the launcher container is running on the Kubernetes cluster or locally
# NOTE running locally doesn't work yet!
IN_CLUSTER = None
# provides functions for creating Deployments
k8s_apps_v1 = None
# provides functions for creating Services
k8s_api_v1 = None
# provides function for modifying an Ingress
k8s_api_networking_v1 = None
# loader for loading templates with jinja2
env = Environment(loader=FileSystemLoader('hebi-manifest-templates'))

# for performing LDAP queries that get info about the user requesting a session
ldap_server_url = 'ldap://ldap.diamond.ac.uk'
ldap_server = Server(ldap_server_url, get_info=ALL)

# for decrypting the JWT in the browser cookie for requests coming from the
# launcher web app (rather than from SynchWeb)
JWT_ALGORITHM = 'HS256'

# magic numbers related to heartbeat service
# the interval at which to broadcast to all Hebi sessions to check if they are
# active, in seconds
ALL_SESSIONS_CHECK_INTERVAL = int(os.environ['ALL_SESSIONS_CHECK_INTERVAL'])
# the interval at which to check the "last seen active timestamp" of all
# sessions, in seconds
INACTIVE_SESSION_CHECK_INTERVAL = int(os.environ['INACTIVE_SESSION_CHECK_INTERVAL'])
# if a user's session has been inactive for a time longer than this value (in
# seconds), then it will be deemed to be inactive and the associated k8s
# resources will be deleted
# real value
SESSION_INACTIVITY_PERIOD_HRS = int(os.environ['SESSION_INACTIVITY_PERIOD_HRS'])
SESSION_INACTIVITY_PERIOD_DAYS = int(os.environ['SESSION_INACTIVITY_PERIOD_DAYS'])
# the interval at which to write the all_sessions_activity dict to file
WRITE_SESSION_ACTIVITY_INTERVAL = 300
SESSION_ACTIVITY_FILE_PATH = '/persistent_data/all_sessions_activity.pkl'

APP_DIR = ''
logger = None


def setup_logger():
    formatter = logging.Formatter(
        "[%(asctime)s] {%(pathname)s:%(lineno)d} %(levelname)s - %(message)s")

    if IN_CLUSTER == 'True':
        if not os.path.exists('/tmp/log'):
            os.mkdir('/tmp/log')
        log_file_path = '/tmp/log/hebi-launcher.log'
    else:
        log_file_path = os.path.join(APP_DIR, 'log/hebi-launcher.log')

    handler = RotatingFileHandler(log_file_path, maxBytes=10000000,
                                  backupCount=5)
    handler.setLevel(logging.INFO)
    handler.setFormatter(formatter)
    log = logging.getLogger('LAUNCHER')
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return log


def get_current_ingress_config():
    '''
    Form a python dict representing the current configuration of the Ingress
    that routes HTPP traffic for Hebi sessions
    '''
    # get Ingress details
    ingress = k8s_api_networking_v1.list_namespaced_ingress(
        namespace='hebi', pretty='true'
    )

    # get apiVersion
    if len(ingress.items[0].metadata.managed_fields) != 0:
        # get apiVersion from the very first application of the Ingress manifest
        last_ingress_api_version = ingress.items[0].metadata.managed_fields[0].api_version
    else:
        # otherwise, assume that it's 'networking.k8s.io/v1'
        last_ingress_api_version = 'networking.k8s.io/v1'

    # get last annotations and name from the last metadata
    last_ingress_metadata = {
        'name': ingress.items[0].metadata.name,
        'annotations': ingress.items[0].metadata.annotations
    }

    # grab spec info from spec.__repr__(), since spec by itself is not a JSON
    # string, it's a ExtensionsV1IngressSpec object
    # to make this a valid JSON string, need to:
    # - replace siongle quotes with double quotes
    # - change instances of 'None' to 'null'
    # NOTE: using spec.__dict__() would probably be a simpler approach
    last_ingress_spec_str = ingress.items[0].spec.__repr__().replace('\'', '\"').replace('None', 'null')
    last_ingress_spec_dict = json.loads(last_ingress_spec_str)

    # the path type in routes are in "snake case" (underscores are separators)
    # in the Ingress spec object, but patches require names to be in "camel
    # case", so it needs to be converted to camel case for every route found in
    # the Ingress before trying to apply a patch
    if last_ingress_spec_dict['rules'][0]['http'] is not None:
        # iterate over the dicts in the 'paths' list that represent user routes
        # and change the snake case to camel case
        for route in last_ingress_spec_dict['rules'][0]['http']['paths']:
            route['pathType'] = route.pop('path_type')

    # put together a python dict representing the current Ingress config
    ingress_config = {
        'apiVersion': last_ingress_api_version,
        'kind': 'Ingress',
        'metadata': last_ingress_metadata,
        'spec': last_ingress_spec_dict
    }

    return ingress_config


def add_route_to_ingress(ingress_config, fedid):
    '''
    Add route to Ingress for user's Service based on their FedID
    '''

    # define vars for modifying ingress
    namespace = 'hebi'
    field_manager = 'hebi-launcher'

    route = {
        'path': f"/{fedid}(/|$)(.*)",
        'pathType': 'Prefix',
        'backend': {
            'service': {
                'name': 'hebi-service-' + fedid,
                'port': {
                    'number': 8080
                }
            }
        }
    }

    # check if the Ingress has the list spec.rules[0].http.paths defined; if
    # not, need to first add it before appending the route
    if ingress_config['spec']['rules'][0]['http'] is None:
        ingress_config['spec']['rules'][0]['http'] = {
            'paths': []
        }

    # add route to dict
    ingress_config['spec']['rules'][0]['http']['paths'].append(route)

    # add route to Ingress resource
    try:
        patch = k8s_api_networking_v1.patch_namespaced_ingress(
            'hebi-ingress', namespace, ingress_config, pretty='true',
            field_manager=field_manager
        )
        logger.info(f"Ingress path added for {fedid}")
    except ApiException as ae:
        err_str = f"Exception when calling " \
                  f"ExtensionsV1Api->patch_namespaced_ingress: {str(ae)}"
        logger.error(err_str)
        print(err_str)


def remove_route_from_ingress(ingress_config, fedid):
    '''
    Remove route to Ingress for user's Service based on their FedID
    '''

    # define vars for modifying ingress
    namespace = 'hebi'
    field_manager = 'hebi-launcher'

    paths = ingress_config['spec']['rules'][0]['http']['paths']
    for index, route in enumerate(paths):
        if route['path'] == f"/{fedid}(/|$)(.*)":
            del paths[index]

    # check if there are no paths left; if so, need to remove the empty
    # http.paths value from the Ingress config entirely, since it doesn't like
    # having an empty list of paths, nor an empty dict for http
    if len(paths) == 0:
        ingress_config['spec']['rules'][0] = {
            'host': 'hebi.diamond.ac.uk'
        }

    # remove route
    try:
        # NOTE: patching seemingly has a bug where if:
        # - there is one rewrite-rule in 'nginx.org/rewrites'
        # - the user associated to that rewrite-rule then removes their Hebi
        #   session, thus this flask app needs to remove the entire
        #   'nginx.org/rewrites' key in the Ingress' annotations dict
        #
        # then the patch that removes the 'nginx.org/rewrites' does NOT get
        # "seen" by k8s as having changed the config for some reason, and thus
        # the patch is not applied, so the Ingress is not updated
        # This behaviour can also be seen when using the kubectl command line
        # tool and attempting to use apply -f to make the analogous patch but
        # in a YAML file
        # kubectl version info when the problem occured:
        # client "GitVersion": 1.20.4
        # server "GitVersion": 1.20.4
        # 
        # Using replace_namespaced_ingress() can get around this problem, but
        # then causes issues with the Ingress not performing routing correctly
        # anymore once it has been used: likely there is some other config that
        # needs to be included in the patch to keep the Ingress working, but I
        # am unsure what it is (the alternative being to include everything in
        # a NetworkingV1Ingress object):
        # https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/NetworkingV1Api.md#replace_namespaced_ingress
        patch = k8s_api_networking_v1.patch_namespaced_ingress(
            'hebi-ingress', namespace, ingress_config, pretty='true',
            field_manager=field_manager
        )
        logger.info(f"Ingress path removed for {fedid}")
    except ApiException as ae:
        err_str = f"Exception when calling " \
                  f"ExtensionsV1Api->patch_namespaced_ingress: {str(ae)}"
        logger.error(err_str)
        print(err_str)


def get_user_ldap_info(fedid):
    '''
    Collect some info about the requestor using LDAP queries to ensure that the
    user is:
    - either a member of the dls_staff group, or a visit user (no check
      implemented for this yet)
    - not root
    - not a member of the dls_sysadmin group
    - not a member of the functional_accounts group
    '''

    user_info = {}

    uid_search_dn = 'ou=people,dc=diamond,dc=ac,dc=uk'
    uid_search_filter = '(uid=' + fedid + ')'
    uid_search_attrs = ['uidNumber']
    group_search_dn = 'ou=group,dc=diamond,dc=ac,dc=uk'
    group_search_attrs = ['memberUid']
    conn = Connection(ldap_server)

    if conn.bind() is True:
        # get user's UID
        uid_search_res = conn.search(uid_search_dn,
            uid_search_filter,
            attributes=uid_search_attrs)
        user_info['uid'] = conn.entries[0]['uidNumber'].value
        user_info['is_uid_root'] = (user_info['uid'] == 0)

        # check if the user is a member of dls_staff
        dls_staff_search_res = conn.search(group_search_dn,
            '(cn=dls_staff)',
            attributes=group_search_attrs)
        user_info['is_dls_staff_member'] = \
            fedid in conn.entries[0]['memberUid'].value

        # check if the user is a member of dls_sysadmin
        dls_sysadmin_search_res = conn.search(group_search_dn,
            '(cn=dls_sysadmin)',
            attributes=group_search_attrs)
        user_info['is_dls_sysadmin_member'] = \
            fedid in conn.entries[0]['memberUid'].value

        # check if the user is a member of functional_accounts
        function_accounts_search_res = conn.search(group_search_dn,
            '(cn=functional_accounts)',
            attributes=group_search_attrs)
        user_info['is_functional_accounts_member'] = \
            fedid in conn.entries[0]['memberUid'].value
    else:
        print('failed ldap server bind: %s' % conn.result)

    conn.unbind()

    return user_info


@socketio.on('session-connect')
def session_connected(data):
    '''
    Update the "last seen active timestamp" of the client that has connected to
    the launcher by sending the session-connect event
    '''
    update_session_last_active_timestamp(data['client'])


@socketio.on('heartbeat-response')
def heartbeat_response(data):
    '''
    Update the "last seen active timestamp" of the client responding to the
    heartbeat-request event
    '''
    update_session_last_active_timestamp(data['client'])


def update_session_last_active_timestamp(url):
    '''
    Given the URL of the webpage in a user's Hebi session in their browser,
    update the timestamp of the correspionding Hebi session
    '''
    # update the timestamp of that user's Pod in all_sessions_activity
    user = get_user_from_session_url(url)
    thread_lock.acquire()
    all_sessions_activity[user] = datetime.now()
    thread_lock.release()


def get_user_from_session_url(url):
    '''
    Get the owner of the session that has responded to the "heartbeat
    request/check" from the URL that the clinet responded with
    '''
    # could use regexes to do something more reliable than string splitting
    return url.split('/')[3]


def check_all_sessions_activity():
    '''
    Broadcast a message to all listening Hebi sessions to check for
    activity/inactivity
    '''
    while True:
        socketio.emit('heartbeat-request', {'data': 'Are you active?'})
        socketio.sleep(ALL_SESSIONS_CHECK_INTERVAL)


def get_all_running_user_pods():
    '''
    Get a list of all the users who have Hebi Pods currently running
    '''
    all_pods = k8s_api_v1.list_namespaced_pod(namespace='hebi')
    all_users_with_running_pods = []
    for pod in all_pods.items:
        # exclude Pods that are in the process of shutting down
        if 'launcher' not in pod.metadata.labels['app'] and pod.metadata.deletion_timestamp is None:
            user = pod.metadata.labels['app'].split('-')[1]
            all_users_with_running_pods.append(user)
    return all_users_with_running_pods


def check_if_pod_is_active(fedid):
    '''
    Check the timestamp of the last time that the user's session responded to a
    heartbeat-request event, and compare it to the current time
    '''
    last_response = all_sessions_activity[fedid]
    current_time = datetime.now()
    difference = current_time - last_response
    if difference.seconds + difference.days * 60 * 60 * 24 < \
            SESSION_INACTIVITY_PERIOD_HRS * 60 * 60 + \
            SESSION_INACTIVITY_PERIOD_DAYS * 60 * 60 * 24:
        return True
    else:
        return False


def check_for_inactive_sessions():
    '''
    Go through all running Hebi Pods and check if their last known time of
    activity is beyond the threshold to be considered inactive, and thus should
    be shutdown
    '''
    while True:
        all_users_with_running_pods = get_all_running_user_pods()
        for user in all_users_with_running_pods:
            thread_lock.acquire()
            try:
                if not check_if_pod_is_active(user):
                    # shutdown k8s resources for the user's Hebi session
                    info_str = f"{user}'s Hebi session has been inactive " \
                               f"for a period of time longer than "\
                               f"SESSION_INACTIVITY_PERIOD_DAYS=" \
                               f"{SESSION_INACTIVITY_PERIOD_DAYS} days and " \
                               f"SESSION_INACTIVITY_PERIOD_HRS=" \
                               f"{SESSION_INACTIVITY_PERIOD_HRS} hours;" \
                               f"shutting it down and removing all k8s " \
                               f"resources related to this Hebi session."
                    logger.info(info_str)
                    delete_hebi_k8s_resources(user)
            except KeyError as e:
                # possibly because the launcher restarted and hasn't grabbed the
                # latest heartbeat-response, so there should be some mechanism
                # to allow for a few bad attempts like this before deleting the
                # session, sicne the launcher may have just restarted
                err_str = f"{user}'s Hebi session wasn't found in " \
                          f"all_sessions_activity: {str(e)}"
                logger.error(err_str)
                print(err_str)
            thread_lock.release()
        socketio.sleep(INACTIVE_SESSION_CHECK_INTERVAL)


def write_session_activity_to_file():
    """
    Periodically write the `all_sessions_activity` dict to file, so then its
    information can persist over restarts of the launcher app, and thus
    inactive sessions can be detected correctly even if the launcher app
    restarts.
    """
    while True:
        with open(SESSION_ACTIVITY_FILE_PATH, 'wb') as f:
            pickle.dump(all_sessions_activity, f)
        socketio.sleep(WRITE_SESSION_ACTIVITY_INTERVAL)


@app.route('/k8s/session_info')
def get_user_session_info():
    '''
    Determine if the user who has visited the launcher web app has a Hebi
    session already running or not
    '''
    cookie = request.cookies.get('token')
    payload = jwt.decode(cookie, os.environ['JWT_KEY'], algorithms=[JWT_ALGORITHM])
    fedid = payload['username']
    resp = {
        'username': fedid        
    }
    all_users_with_running_pods = get_all_running_user_pods()

    if fedid in all_users_with_running_pods:
        resp['is_session_currently_running'] = True
    else:
        resp['is_session_currently_running'] = False

    return json.dumps(resp)


@app.route('/k8s/start_hebi')
def start_hebi():
    '''
    Create the required k8s resources for the user requesting to run Hebi
    '''

    data = request.args.to_dict()

    # check if FedID is in the request or not; if not, it's in the cookie in
    # the web browser
    if 'fedid' not in data:
        cookie = request.cookies.get('token')
        payload = jwt.decode(cookie, os.environ['JWT_KEY'], algorithms=[JWT_ALGORITHM])
        fedid = payload['username']
    else:
        fedid = data['fedid']

    user_ldap_info = get_user_ldap_info(fedid)
    logger.info(f"LDAP info for {fedid}: {user_ldap_info}")

    # perform some checks on the requestor before a Hebi session is allowed to
    # be launched for them
    is_valid_user = user_ldap_info['is_dls_staff_member'] \
        and not user_ldap_info['is_uid_root'] \
        and not user_ldap_info['is_dls_sysadmin_member'] \
        and not user_ldap_info['is_functional_accounts_member']

    if not is_valid_user:
        # don't launch a session, and report back to the launcher web app with
        # the ldap info for debugging
        response = {
            'username': fedid,
            'was_session_launched': False,
            'message': 'Invalid user, see user_ldap_info for more info',
            'user_ldap_info': user_ldap_info
        }
        return json.dumps(response)

    # otherwise, if is_valid_user is true, then a session for the user can be
    # launched

    # check if UID is in request or not; if not, grab it from user_ldap_info
    if 'uid' not in data:
        uid = user_ldap_info['uid']
    else:
        uid = data['uid']

    # check if the user already has a session running before attempting to
    # launch one
    user_pods = k8s_api_v1.list_namespaced_pod(
            namespace='hebi',
            label_selector='app={}'.format('hebi-' + fedid))
    is_user_pod_present = (user_pods.items != [])

    user_services = k8s_api_v1.list_namespaced_service(
            namespace='hebi',
            field_selector='metadata.name={}'.format('hebi-service-' + fedid))
    is_user_service_present = (user_services.items != [])

    if is_user_pod_present and is_user_service_present:
        response = {
            'username': fedid,
            'was_session_launched': False,
            'is_hebi_pod_running': True,
            'message': 'session exists'
        }
        return json.dumps(response)

    # create Service
    service_template = env.get_template('service.yaml')
    service_yaml = service_template.render(fedid=fedid)

    service_doc = yaml.safe_load(service_yaml)
    try:
        resp = k8s_api_v1.create_namespaced_service(
            body=service_doc, namespace='hebi'
        )
        logger.info(f"Service created for {fedid}: {resp.metadata.name}")
    except ApiException as ae:
        err_str = f"Something went wrong with creating the Service for " \
                  f"{fedid}'s Hebi session: {str(ae)}"
        logger.error(err_str)
        print(err_str)

    # add route to this new Service to the Ingress
    ingress_config = get_current_ingress_config()
    add_route_to_ingress(ingress_config, fedid)

    # create Deployment
    deployment_template = env.get_template('deployment.yaml')
    deployment_vars = {
        'fedid': fedid,
        'uid': uid,
        'gid': uid,
        'service': 'https://hebi.diamond.ac.uk/' + fedid + '/',
        'cas_server': 'https://auth.diamond.ac.uk/cas',
        'websocket_server': 'https://hebi.diamond.ac.uk'
    }
    deployment_yaml = deployment_template.render(deployment_vars)

    deployment_doc = yaml.safe_load(deployment_yaml)
    try:
        resp = k8s_apps_v1.create_namespaced_deployment(
            body=deployment_doc, namespace='hebi'
        )
        logger.info(f"Deployment created for {fedid}: {resp.metadata.name}")
    except ApiException as ae:
        err_str = f"Something went wrong with creating the Deployment for "\
                  f"{fedid}'s Hebi session: {str(ae)}"
        logger.error(err_str)
        print(err_str)

    # Poll for pod status on startup
    watch_pod = watch.Watch()
    for event in watch_pod.stream(
            k8s_api_v1.list_namespaced_pod,
            namespace='hebi',
            label_selector='app={}'.format('hebi-' + fedid)):
        status = event['object'].status.phase
        if status == 'Running':
            watch_pod.stop()
            logger.info(f"Pod in {fedid}'s Deployment is now running")
            break

    response = {
        'username': fedid,
        'was_session_launched': True,
        'is_hebi_pod_running': True
    }

    return json.dumps(response)


@app.route('/k8s/stop_hebi')
def stop_hebi():
    '''
    View function to delete the relevant k8s resources for the user requesting
    to terminate their Hebi session

    It may or may not be necessary for users to be able to manually stop their
    session, the "heartbeat" service that cleans up resources could turn out to
    be sufficient
    '''

    data = request.args.to_dict()

    # check if FedID is in the request or not; if not, it's in the cookie in
    # the web browser
    if 'fedid' not in data:
        cookie = request.cookies.get('token')
        payload = jwt.decode(cookie, os.environ['JWT_KEY'], algorithms=[JWT_ALGORITHM])
        fedid = payload['username']
    else:
        fedid = data['fedid']

    response = delete_hebi_k8s_resources(fedid)

    return json.dumps(response)


def delete_hebi_k8s_resources(fedid):
    '''
    Delete the relevant k8s resources of a user
    '''
    log_session_stop = {
        'username': fedid,
        'was_session_stopped': False
    }
    try:
        # delete Deployment
        deployment_name = 'hebi-' + fedid
        resp = k8s_apps_v1.delete_namespaced_deployment(
            name=deployment_name, namespace='hebi', pretty='true',
            grace_period_seconds=0, propagation_policy='Background'
        )
        logger.info(f"Deployment deleted for {fedid}: {deployment_name}")

        # delete Service
        service_name = 'hebi-service-' + fedid
        resp = k8s_api_v1.delete_namespaced_service(
            name=service_name, namespace='hebi', pretty='true',
            grace_period_seconds=0, propagation_policy='Background'
        )
        logger.info(f"Service deleted for {fedid}: {service_name}")

        # remove route to this deleted Service from the Ingress
        ingress_config = get_current_ingress_config()
        remove_route_from_ingress(ingress_config, fedid)

        log_session_stop['was_session_stopped'] = True
        log_session_stop['did_session_exist'] = True

        # remove the user's session timestamp info from all_sessions_activity
        all_sessions_activity.pop(fedid, None)
    except ApiException as ae:
        err_str = f"Something went wrong with stopping a Hebi session when " \
                  f"interacting with k8s: {str(ae)}"
        logger.error(err_str)
        print(err_str)
        if ae.reason == 'Not Found':
            log_session_stop['did_session_exist'] = False

    return log_session_stop


def main(argv):
    global IN_CLUSTER, k8s_apps_v1, k8s_api_v1, k8s_api_networking_v1, \
        env, ldap_server, all_sessions_activity, thread_lock, logger, APP_DIR

    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    IN_CLUSTER = os.environ['IN_CLUSTER']

    if IN_CLUSTER == 'True':
        config.load_incluster_config()
        k8s_apps_v1 = client.AppsV1Api()
        k8s_api_v1 = client.CoreV1Api()
        k8s_api_networking_v1 = client.NetworkingV1Api()
    else:
        configuration = client.Configuration()
        configuration.host = "http://localhost:8090"
        k8s_apps_v1 = client.AppsV1Api(client.ApiClient(configuration=configuration))
        k8s_api_v1 = client.CoreV1Api(client.ApiClient(configuration=configuration))

    logger = setup_logger()

    # attempt to load data from SESSION_ACTIVITY_FILE_PATH into
    # all_sessions_activity
    try:
        with open(SESSION_ACTIVITY_FILE_PATH, 'rb') as f:
            previous_all_sessions_activity = pickle.load(f)
            logger.info(f"Current session timestamps are {all_sessions_activity}, updating all_sessions_activity with timestamps from previous launcher Pod...")
            all_sessions_activity.update(previous_all_sessions_activity)
            logger.info(f"Updated session timestamps are {all_sessions_activity}")
    except FileNotFoundError as e:
        logger.info(f"Didn't find any file at {SESSION_ACTIVITY_FILE_PATH}, assuming that no previous session timestamps exists")

    logger.info('Hebi launcher has started running')

    signal.signal(signal.SIGINT, exit_handler)

    # start socketio background tasks
    heartbeat_poll_thread = socketio.start_background_task(check_all_sessions_activity)
    inactive_session_check_thread = socketio.start_background_task(check_for_inactive_sessions)
    write_session_activity_to_file_thread = socketio.start_background_task(
        write_session_activity_to_file)

    if os.environ['FLASK_MODE'] == 'production':
        socketio.run(app, host='127.0.0.1', port=8085)
    else:
        socketio.run(app, host='0.0.0.0', port=8085, debug=True,
                     use_reloader=True)

def exit_handler(signal, frame):
    logger.info('Hebi launcher is stopping, exiting flask server')
    print('Hebi launcher is stopping, exiting flask server')
    sys.exit(0)

if __name__ == '__main__':
    main(sys.argv[1:])
