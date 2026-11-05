"""Actual viewer module in Chromium: scaled input, safe leases and handoff UI."""
import io
import os
import json
import pytest
from PIL import Image

@pytest.mark.slow
async def test_viewer_take_control_scaled_click_text_and_release():
    from playwright.async_api import async_playwright
    origin=os.environ['ODYSSEUS_TEST_STATIC_ORIGIN']
    pixels=io.BytesIO();Image.new('RGB',(1280,900),'white').save(pixels,format='JPEG')
    state=dict(session_id='viewer-chat',incarnation='instance',page='t1',frame_id='frame0',width=1280,height=900,
               revision=0,mode='agent',controlling=False,tabs=[{'page':'t1','url':'https://catalog.example/'}])
    actions=[]
    async def route(request):
        if '/frame/' in request.request.url:
            await request.fulfill(status=200,content_type='image/jpeg',body=pixels.getvalue());return
        if request.request.method=='POST':
            args=request.request.post_data_json;operation=request.request.url.rsplit('/',1)[-1]
            actions.append((operation,args))
            if operation=='take':state.update(mode='human',controlling=True,lease='secret-lease')
            elif operation=='release':state.update(mode='agent',controlling=False);state.pop('lease',None)
            else:state['frame_id']='frame'+str(len(actions))
        await request.fulfill(status=200,content_type='application/json',body=json.dumps(state))
    async with async_playwright() as pw:
        browser=await pw.chromium.launch()
        page=await browser.new_page()
        await page.route('**/api/browser/**',route)
        await page.goto(origin+'/static/test-fixtures/browser-catalog.html')
        await page.set_content('<div id="viewer" style="width:640px"></div>')
        await page.add_style_tag(url=origin+'/static/css/03-agent-chat.css')
        await page.evaluate("""async () => {
            location.hash='viewer-chat';
            const mod=await import('/static/js/browserViewer.js');window.viewerModule=mod;
            document.querySelector('#viewer').innerHTML=mod.browserPreviewHtml();
        }""")
        await page.get_by_text('Ready — Odysseus controls the browser',exact=True).wait_for()
        await page.get_by_role('button',name='Take control',exact=True).click()
        await page.get_by_text('You’re in control — Odysseus is paused',exact=True).wait_for()
        await page.wait_for_function("document.querySelector('.browser-viewer img').naturalWidth > 0")
        img=page.locator('.browser-viewer img')
        box=await img.bounding_box()
        assert box['width'] < 1280
        await img.click(position={'x':box['width']/2,'y':box['height']/2})
        await page.wait_for_function("document.querySelector('.browser-viewer img').getAttribute('src').endsWith('frame2')")
        click=actions[-1][1]
        assert click['kind']=='click' and abs(click['x']-640)<3 and abs(click['y']-450)<3
        await page.get_by_role('textbox',name='Text for focused browser field').fill('test input')
        await page.get_by_role('button',name='Send text',exact=True).click()
        await page.wait_for_function("document.querySelector('textarea').value === ''")
        assert actions[-1][1]['text']=='test input'
        assert 'secret-lease' not in await page.content()
        assert not await page.locator('.private-browser-preview-spinner').count()
        await page.get_by_role('button',name='Return control to Odysseus',exact=True).click()
        await page.get_by_text('Ready — Odysseus controls the browser',exact=True).wait_for()
        assert actions[-1][0]=='release'
        points=await page.evaluate("""() => ({
            outside:viewerModule.imagePoint({left:0,top:0,width:1000,height:500},1280,900,0,250),
            center:viewerModule.imagePoint({left:0,top:0,width:1000,height:500},1280,900,500,250)
        })""")
        assert points['outside'] is None
        assert points['center']=={'x':640,'y':450}
        await browser.close()
