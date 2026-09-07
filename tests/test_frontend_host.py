from test_open_registration import anonymous


async def test_application_pages_and_api_errors_share_origin():
    async with anonymous() as browser:
        for path in ('/', '/login', '/register', '/app/profile'):
            response = await browser.get(path)
            assert response.status_code == 200
            assert 'text/html' in response.headers['content-type']
            assert '<div id="app">' in response.text
        for path in ('/api/does-not-exist', '/assets/missing.js', '/assets/../backend/config.py'):
            assert (await browser.get(path)).status_code == 404
        assert (await browser.get('/docs')).status_code == 200
        assert (await browser.get('/health')).json() == {'status': 'ok'}
