"""Optional browser check: start tests.browser_demo, then python -m tests.browser_smoke.
Requires playwright and installed Microsoft Edge; not part of runtime dependencies.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1100, 'height': 900})
        page.goto('http://127.0.0.1:8765')
        page.get_by_label('Your question').fill('How many?')
        page.get_by_role('button', name='Ask InsightBot').click()
        page.wait_for_function("document.getElementById('status').textContent === 'Complete.'")
        assert '42' in page.locator('.answer').inner_text()
        assert page.locator('#messages script').count() == 0
        assert 'SELECT 42 AS answer LIMIT 200' in page.locator('#messages pre').text_content()
        page.get_by_label('Your question').fill('And again?')
        page.get_by_role('button', name='Ask InsightBot').click()
        page.wait_for_function("document.getElementById('status').textContent === 'Complete.'")
        assert page.locator('#messages article').count() == 2
        Path('.pytest-browser').mkdir(exist_ok=True)
        page.screenshot(path='.pytest-browser/desktop.png', full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path='.pytest-browser/mobile.png', full_page=True)
        page.get_by_role('button', name='New conversation').click()
        page.wait_for_function("document.getElementById('status').textContent === 'New conversation ready.'")
        assert page.locator('#messages article').count() == 0
        # An SSE error must be visible and re-enable the form.
        page.route('**/chat/stream', lambda route: route.fulfill(
            content_type='text/event-stream', body='data: {"type":"error","message":"Provider unavailable. Retry."}\n\n'))
        page.get_by_label('Your question').fill('Simulate failure')
        page.get_by_role('button', name='Ask InsightBot').click()
        page.wait_for_function("document.getElementById('status').textContent === 'Unable to complete this answer.'")
        assert 'Provider unavailable' in page.locator('.error').inner_text()
        assert page.get_by_role('button', name='Ask InsightBot').is_enabled()
        browser.close()
        print('Browser smoke passed: streamed answer, SQL, follow-up, safe text, reset, mobile layout, error recovery')


if __name__ == '__main__':
    main()
