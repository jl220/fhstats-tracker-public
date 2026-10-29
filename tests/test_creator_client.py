from uuid import uuid4
import pytest

from forza_tracker.api.creator_events import CommunityEventsClient


class Response:
    status_code=200
    def raise_for_status(self):pass
    def json(self):return {'id':str(uuid4()),'status':'PENDING'}


class Session:
    def __init__(self):self.calls=[]
    def post(self,url,**kwargs):self.calls.append((url,kwargs));return Response()


def test_community_transport_has_personal_csrf_origin_and_no_online_key():
    s=Session();client=CommunityEventsClient('https://fhstats.com',session=s,csrf_token='personal-csrf')
    client.submit({'attempt_id':str(uuid4())})
    url,kwargs=s.calls[0]
    assert url=='https://fhstats.com/api/account/creator-events/attempts'
    assert kwargs['headers']=={'Origin':'https://fhstats.com','X-CSRF-Token':'personal-csrf'}
    assert kwargs['allow_redirects'] is False


def test_transport_refuses_missing_personal_session_or_csrf():
    with pytest.raises(ValueError):CommunityEventsClient('https://fhstats.com',session=None,csrf_token='x')
    with pytest.raises(ValueError):CommunityEventsClient('https://fhstats.com',session=Session(),csrf_token='')


def test_submission_pins_expected_account_in_request():
    s=Session();client=CommunityEventsClient('https://fhstats.com',session=s,csrf_token='personal-csrf')
    account=str(uuid4());client.submit({'attempt_id':str(uuid4())},account_id=account)
    assert s.calls[0][1]['headers']['X-FHStats-Account-ID']==account


def test_password_login_uses_own_session_cookies_without_saving_password():
    class LoginSession(Session):
        def __init__(self):super().__init__();self.cookies={}
        def post(self,url,**kwargs):
            self.calls.append((url,kwargs));self.cookies['__Host-fhstats_csrf']='fresh-csrf'
            class LoginResponse(Response):
                def json(self):return {'authenticated':True}
            return LoginResponse()
    s=LoginSession()
    client=CommunityEventsClient.login('https://fhstats.com','test@example.com','secret',session=s)
    client.submit({'attempt_id':str(uuid4())})
    assert s.calls[0][0].endswith('/api/auth/login')
    assert s.calls[0][1]['allow_redirects'] is False
    assert client.csrf=='fresh-csrf' and not hasattr(client,'password')
