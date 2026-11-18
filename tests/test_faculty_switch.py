from tests.test_faculty_acceptance import portal, sign_in, send, decide, page


def test_switch_authenticated_accounts_complete_swap_then_logout(portal, app):
    a, other_browser = portal
    assert 'value="FB"' not in page_switch(a)
    assert a.post('/switch-faculty', data={'faculty_id': 'FB'}).status_code == 403
    sign_in(a, 'FB', 'wrong-password')
    assert 'value="FB"' not in page_switch(a)
    sign_in(a, 'FB')
    assert 'value="FA"' in page_switch(a) and 'value="FB"' in page_switch(a)
    assert 'value="FA"' not in page_switch(other_browser)
    a.post('/switch-faculty', data={'faculty_id': 'FA'})
    send(a)
    a.post('/switch-faculty', data={'faculty_id': 'FB'})
    assert '>approved</span>' in decide(a, 'approve')
    assert 'EARLY' in page(a, 'timetable')
    a.post('/logout')
    assert 'value="FA"' in page_switch(a) and 'value="FB"' not in page_switch(a)
    assert a.post('/switch-faculty', data={'faculty_id': 'FB'}).status_code == 403
    a.post('/switch-faculty', data={'faculty_id': 'FA'})
    assert 'EARLY' not in page(a, 'timetable')
    a.post('/logout')
    assert a.get('/switch-faculty').location.endswith('/login?portal=faculty')


def page_switch(browser):
    return browser.get('/switch-faculty').get_data(as_text=True)


def test_logout_selected_first_account_leaves_second(portal):
    browser, _ = portal
    sign_in(browser, 'FB')
    response = browser.post('/switch-faculty', data={'faculty_id': 'FA'}, follow_redirects=True)
    assert b'Faculty Dashboard' in response.data
    assert b'EARLY' in response.data and b'OTHER' not in response.data
    chooser = browser.post('/logout', follow_redirects=True).get_data(as_text=True)
    assert 'value="FA"' not in chooser and 'value="FB"' in chooser
    browser.post('/switch-faculty', data={'faculty_id': 'FB'})
    assert 'OTHER' in page(browser, 'timetable')
