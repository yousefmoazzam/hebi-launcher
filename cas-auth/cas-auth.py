import os
import requests
import jwt
import sys

from flask import Flask, request, jsonify, abort
from flask_cors import cross_origin, CORS

app = Flask(__name__)
CORS(app, support_credentials=True)

SERVICE = "https://hebi.diamond.ac.uk/launcher/"
CAS_SERVER = "https://auth.diamond.ac.uk/cas"
CAS_VALIDATE_URL = "{}/serviceValidate".format(CAS_SERVER)
JWT_ALGORITHM = 'HS256'


def process_token(token):
    '''
    Decode the JWT that is the cookie in the user's web browser
    '''
    try:
        payload = jwt.decode(token, os.environ['JWT_KEY'], algorithms=[JWT_ALGORITHM])
    except Exception as e:
        raise KeyError(str(e))
    return payload


@app.route('/')
def check_for_cookie():
    '''
    Check if the HTTP request that came from the launcher web app in the
    browser has a cookie that denotes if a user has authenticated to the
    launcher
    '''
    cookie = request.cookies.get('token')
    payload = {}

    if cookie is None:
        # unauthorised user requesting access
        abort(403)
    else:
        # check the token to see if the 'username' value in it matches the
        # owner of the Hebi session (which is defined in the FEDID env var)
        decoded_token = process_token(cookie)

        if 'username' not in decoded_token:
            # something is wrong with the token, so deny access
            payload['has_requestor_been_authenticated'] = False
            return jsonify(payload)
        else:
            # token has the username, so they have been authenticated to get to
            # the launcher page
            payload['has_requestor_been_authenticated'] = True
            payload['username'] = decoded_token['username']

        return jsonify(payload)


@app.route('/validate_ticket')
def validate_ticket():
    '''
    Validate a ticket that was handed to the user's web browser by the CAS
    server
    '''
    data = request.args.to_dict()
    params = {
        'format': 'json',
        'ticket': data['ticket'],
        'service': SERVICE
    }
    auth_req = requests.get(CAS_VALIDATE_URL, params=params)

    # used for holding info about the validation request
    output_dict = {
        'validated': False        
    }

    # check the CAS server response to the validation request
    try:
        auth_resp = auth_req.json()
    except Exception as e:
        output_dict['desc'] = 'invalid_CAS_server_response'
        output_dict['validated'] = False
        return jsonify(output_dict)

    if 'authenticationSuccess' in auth_resp['serviceResponse']:
        username = auth_resp['serviceResponse']['authenticationSuccess']['user']
        output_dict['validated'] = True
        output_dict['user'] = username
        output_dict['desc'] = 'successful authentication'

        # create a token from the ticket validation that has the user's FedID
        # stored in it
        payload = {
            'username': username
        }
        token = jwt.encode(payload, os.environ['JWT_KEY'], algorithm=JWT_ALGORITHM)
        output_dict['token'] = token
        resp = jsonify(output_dict)

        # set a cookie in the client's web browser
        resp.set_cookie('token', token)
    elif 'authenticationFailure' in auth_resp['serviceResponse']:
        output_dict['validated'] = False
        output_dict['code'] = auth_resp['serviceResponse']['authenticationFailure']['code']
        output_dict['desc'] = auth_resp['serviceResponse']['authenticationFailure']['description']
        resp = jsonify(output_dict)
    else:
        # something else went wrong
        output_dict['validated'] = False
        output_dict['desc'] = 'invalid_CAS_server_response'
        resp = jsonify(output_dict)

    return resp


def main(argv):

    app.run(host='0.0.0.0', port=8086, debug=True, use_reloader=True,
        threaded=True)


if __name__ == '__main__':
    main(sys.argv[1:])
