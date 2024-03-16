import time
import threading
import queue
from pathlib import Path
from playwright.sync_api import sync_playwright
from my_logger import logger

MS_URL = 'https://game.maj-soul.com/1/'
DATA_FOLDER = 'data'

class Browser:
    """ Wrapper for Playwright browser control"""  
    def __init__(self, playwright_width:int, playwright_height:int, mitm_host:str, mitm_port:int):
        """ Initialize and open the browser"""
        self.width = playwright_width
        self.height = playwright_height
        self.proxy = f"{mitm_host}:{mitm_port}"
        
        self.action_queue = queue.Queue()       # thread safe queue for actions
        self._stop_event = threading.Event()    # set this event to stop processing actions
        self.browser_thread = None
        self.page = None
    
    def __del__(self):
        self.shutdown_browser()                
    
    def start_browser(self):
        """ Launch browser in a thread, and start processing action queue """
        # using thread here to avoid playwright sync api not usable in async context (textual) issue
        self.browser_thread = threading.Thread(
            name = "Browser Thread",
            target=self._run_browser_and_action_queue,
            daemon=True)
        self.browser_thread.start()

    
    def _run_browser_and_action_queue(self):
        """ run browser and keep processing action queue (blocking)"""
        
        logger.info(f'Starting Chromium browser, resolution={self.width}x{self.height}, mitm_proxy={self.proxy}')
        with sync_playwright() as playwright:            
            chromium = playwright.chromium
            browser = chromium.launch_persistent_context(
                user_data_dir=Path(__file__).parent / DATA_FOLDER,
                headless=False,
                viewport={'width': self.width, 'height': self.height},
                proxy={"server": f"{self.proxy}"},
                ignore_default_args=['--enable-automation']
            )
            try:
                self.page = browser.new_page()
                self.page.goto(MS_URL)
                logger.info(f'go to page success, url: {self.page.url}')  
            except Exception as e:
                logger.error(f'Error opening page:')
                logger.error(e)
                logger.error(f'Check if MITM CA certificate is installed')
                  
            logger.info(f'Start processing action queue')
            
            # Keep polling the queue and running actions until stop event is set
            while self._stop_event.is_set() == False:
                try:
                    action = self.action_queue.get(timeout=1)
                    action()
                except queue.Empty:                
                    pass
                except Exception as e:
                    logger.error(f'Error processing action:')
                    logger.error(e)
                    # TODO: tell the user about the error
            
            # stop event is set: close browser  
            logger.info(f'Exiting browser thread')
            self.page.close()
            browser.close()

        return
            
    def clear_action_queue(self):        
        """ Clear the action queue"""
        while True:
            try:
                self.action_queue.get_nowait()
            except queue.Empty:
                break
            
    def shutdown_browser(self):
        """ shutdown browser and clean up"""
        logger.info("Shutting down browser")
        if self.browser_thread:
            self._stop_event.set()
            self.clear_action_queue()
            self.browser_thread.join()
            self.browser_thread = None
            self.page = None

    def mouse_click(self, x:int, y:int):
        """ Add mouse click action to queue"""
        self.action_queue.put(lambda: self._mouse_click_action(x, y))
        
    def auto_hu(self):
        """ Add autohu action to queue"""
        self.action_queue.put(lambda: self._auto_hu_action())
        
    def _mouse_click_action(self, x:int, y:int):
        """ mouse click on page at (x,y)"""
        self.page.mouse.move(x=x, y=y)
        time.sleep(0.15)
        self.page.mouse.click(x=x, y=y, delay=100)
        time.sleep(0.05)
        self.page.mouse.move(x=self.width/2, y=self.height/2)   # move mouse to center
    
    def _auto_hu_action(self):
        """ call autohu function in page"""
        self.page.evaluate("() => view.DesktopMgr.Inst.setAutoHule(true)")
        
        
if __name__ == '__main__':
    # Test for Browser
    browser = Browser(1600, 900, "http://10.0.0.32", 8002)
    browser.start_browser()
    while True:
        numbers_input = input("Enter x y :")
        numbers = numbers_input.split()
        x = int(numbers[0])
        y = int(numbers[1])
        if x==0 and y==0:
            break
        print(f"Clicking x={x}, y={y}")
        browser.mouse_click(x, y)
    browser.shutdown_browser()