"""Personal account transport for the separate community attempts endpoint."""
from urllib.parse import urlsplit


class GameCommunityClient:
    def __init__(self,base_url='https://fhstats.com',*,session=None,timeout_seconds=8):
        import requests
        parsed=urlsplit(base_url)
        if (parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ('','/')):
            raise ValueError('HTTPS community origin required')
        self.base=base_url.rstrip('/');self.timeout=timeout_seconds
        self.session=session or requests.Session()

    def catalog(self):
        return CommunityEventsClient._json(self.session.get(f'{self.base}/api/creator-events/catalog',
                                          timeout=self.timeout,allow_redirects=False))

    def submit_game(self,payload):
        return CommunityEventsClient._json(self.session.post(f'{self.base}/api/creator-events/attempts',
                                          json=payload,timeout=self.timeout,allow_redirects=False))

    def close(self):self.session.close()


class CommunityEventsClient:
    @classmethod
    def login(cls, base_url, email, password, *, session=None):
        import requests
        session=session or requests.Session()
        try:
            # Validate the destination before transmitting credentials.
            client=cls(base_url,session=session,csrf_token='login-pending')
            response=session.post(f'{client.base}/api/auth/login',json={'email':email,'password':password},
                                  headers={'Origin':client.origin},timeout=client.timeout,allow_redirects=False)
            if cls._json(response).get('authenticated') is not True:raise ValueError('login failed')
            client.csrf=session.cookies.get('__Host-fhstats_csrf')
            if not client.csrf:raise ValueError('personal CSRF cookie missing')
            return client
        except Exception:
            if hasattr(session,'close'):session.close()
            raise

    def __init__(self, base_url, *, session, csrf_token, timeout_seconds=8):
        parsed=urlsplit(base_url)
        if (session is None or not csrf_token or parsed.scheme!='https'
                or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('personal HTTPS session and CSRF token required')
        self.base=base_url.rstrip('/')
        self.origin=f'{parsed.scheme}://{parsed.netloc}'
        self.session=session
        self.csrf=csrf_token
        self.timeout=timeout_seconds

    def eligibility(self):
        response=self.session.get(f'{self.base}/api/account/creator-events/eligibility',
                                  timeout=self.timeout,allow_redirects=False)
        return self._json(response)

    def catalog(self):
        response=self.session.get(f'{self.base}/api/creator-events/catalog',
                                  timeout=self.timeout,allow_redirects=False)
        return self._json(response)

    def submit(self, payload, *, account_id=None, ownership_id=None):
        headers={'Origin':self.origin,'X-CSRF-Token':self.csrf}
        if account_id is not None:headers['X-FHStats-Account-ID']=account_id
        if ownership_id is not None:headers['X-FHStats-Ownership-ID']=ownership_id
        response=self.session.post(f'{self.base}/api/account/creator-events/attempts',json=payload,
                                   headers=headers,
                                   timeout=self.timeout,allow_redirects=False)
        return self._json(response)

    def close(self):
        self.session.close()

    @staticmethod
    def _json(response):
        if 300<=response.status_code<400:
            raise ValueError('community API redirect rejected')
        response.raise_for_status()
        return response.json()
