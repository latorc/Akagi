import json
import threading
import asyncio
import time
import re
import os
import pathlib
import subprocess
import mitmproxy.addonmanager
import mitmproxy.http
import mitmproxy.log
import mitmproxy.tcp
import mitmproxy.websocket

from mitmproxy import proxy, options, ctx
from mitmproxy.tools.dump import DumpMaster
from xmlrpc.server import SimpleXMLRPCServer

from my_logger import logger
from browser import Browser

global_activated_flows = [] # store all flow.id ([-1] is the recently opened)
global_messages_dict = dict() # flow.id -> Queue[flow_msg]

class ClientWebSocket:
    """ mitm addon for websocket"""

    def __init__(self):
        pass

    def websocket_start(self, flow: mitmproxy.http.HTTPFlow):
        assert isinstance(flow.websocket, mitmproxy.websocket.WebSocketData)
        global global_activated_flows,global_messages_dict
        
        global_activated_flows.append(flow.id)
        global_messages_dict[flow.id]=[]

    def websocket_message(self, flow: mitmproxy.http.HTTPFlow):
        assert isinstance(flow.websocket, mitmproxy.websocket.WebSocketData)
        global global_activated_flows,global_messages_dict

        global_messages_dict[flow.id].append(flow.websocket.messages[-1].content)

    def websocket_end(self, flow: mitmproxy.http.HTTPFlow):
        global global_activated_flows,global_messages_dict
        global_activated_flows.remove(flow.id)
        global_messages_dict.pop(flow.id)

class ClientHTTP:
    """ mitm addon"""
    def __init__(self):
        pass

    def request(self, flow: mitmproxy.http.HTTPFlow):
        if flow.request.method == "GET":
            if re.search(r'^https://game\.maj\-soul\.(com|net)/[0-9]+/v[0-9\.]+\.w/code\.js$', flow.request.url):
                print("====== GET code.js ======"*3)
                print("====== GET code.js ======"*3)
                print("====== GET code.js ======"*3)
                flow.request.url = "http://cdn.jsdelivr.net/gh/Avenshy/majsoul_mod_plus/safe_code.js"
            elif re.search(r'^https://game\.mahjongsoul\.com/v[0-9\.]+\.w/code\.js$', flow.request.url):
                flow.request.url = "http://cdn.jsdelivr.net/gh/Avenshy/majsoul_mod_plus/safe_code.js"
            elif re.search(r'^https://mahjongsoul\.game\.yo-star\.com/v[0-9\.]+\.w/code\.js$', flow.request.url):
                flow.request.url = "http://cdn.jsdelivr.net/gh/Avenshy/majsoul_mod_plus/safe_code.js"

class LiqiServer:
    """ XMLRPC server that provides an interface for bot to access the intercepted game data.
    It also performs game inputs (actions)"""
    
    _rpc_methods_ = ['get_activated_flows', 'get_messages', 'reset_message_idx', 'page_clicker', 'do_autohu', 'ping']
    def __init__(self, host, port, browser:Browser = None):
        self.host = host
        self.port = port
        self.browser = browser
        
        self.server = SimpleXMLRPCServer((self.host, self.port), allow_none=True, logRequests=False)
        for name in self._rpc_methods_:
            self.server.register_function(getattr(self, name))
        self.message_idx = dict() # flow.id -> int
    
    def __del__(self):
        # shutdown before destruction
        self.shut_down()

    def get_activated_flows(self):
        return global_activated_flows
    
    def get_messages(self, flow_id):
        try:
            idx = self.message_idx[flow_id]
        except KeyError:
            self.message_idx[flow_id] = 0
            idx = 0
        if (flow_id not in global_activated_flows) or (len(global_messages_dict[flow_id])==0) or (self.message_idx[flow_id]>=len(global_messages_dict[flow_id])):
            return None
        msg = global_messages_dict[flow_id][idx]
        self.message_idx[flow_id] += 1
        return msg
    
    def reset_message_idx(self):
        for flow_id in global_activated_flows:
            self.message_idx[flow_id] = 0

    def page_clicker(self, xy):
        if self.browser:
            scale = self.browser.width/16
            x = int(xy[0]*scale)
            y = int(xy[1]*scale)
            self.browser.mouse_click(x, y)
        return True

    def do_autohu(self):
        if self.browser:
            self.browser.auto_hu()
        return True

    def ping(self):
        return True

    def start(self):
        """ Start the XMLRPC server (blocking)"""
        logger.info(f"XMLRPC Server is running on {self.host}:{self.port}")
        self.server.serve_forever()
        
    def shut_down(self):
        logger.info(f"Shutting down XMLRPC Server")
        self.server.shutdown()


class MitmController:
    """ Controlling mitm proxy server and xmlrpc server interactions and managing their threads
    mitm proxy server intercepts data to/from the game server
    XMLRPC server provides an interface for the bot to access the intercepted data
    They are run as daemon threads, which exit when the main thread exits"""
    
    def __init__(self) -> None:
        # Get config options from settings.json
        with open("settings.json", "r") as f:
            settings = json.load(f)
            self.mitm_port = settings["Port"]["MITM"]
            self.rpc_port = settings["Port"]["XMLRPC"]
            self.enable_unlocker = settings["Unlocker"]
            self.enable_helper = settings["Helper"]
            self.enable_playwright = settings["Playwright"]["enable"]
            self.playwright_width = settings["Playwright"]["width"]
            self.playwright_height = settings["Playwright"]["height"]
            self.browser:Browser = None
            
            self.mitm_host="127.0.0.1"
            self.rpc_host="127.0.0.1"
            self.mitm_thread:threading.Thread = None
            self.rpc_thread:threading.Thread = None
            self.dump_master = None
            self.liqi_server:LiqiServer = None            
            
            # Check if 'mitm_config' folder exists, if not, create it
            mitm_config_folder = pathlib.Path(__file__).parent / "mitm_config"
            if not mitm_config_folder.exists():
                mitm_config_folder.mkdir(exist_ok=True)
            self.mitm_config_folder = str(mitm_config_folder.resolve())
        
    def start_mitm(self):
        """ Start mitm server thread"""
        # Update mhmp.json
        logger.info(f"Updating mhmp.json")
        with open("mhmp.json", "r") as f:
            mhmp = json.load(f)
            mhmp["mitmdump"]["mode"] = [f"regular@{self.mitm_port}"]
            mhmp["hook"]["enable_skins"] = self.enable_unlocker
            mhmp["hook"]["enable_aider"] = self.enable_helper
        with open("mhmp.json", "w") as f:
            json.dump(mhmp, f, indent=4)
    
        # Fetch res version  
        import mhm
        logger.info(f"Fetching resver...")
        mhm.fetch_resver()
        
        # Start thread
        self.mitm_thread = threading.Thread(
            name="MITM Thread",
            target=lambda: asyncio.run(self.run_mitm_async()),
            daemon=True
        )
        self.mitm_thread.start()
        
    
    async def run_mitm_async(self):        
        
        opts = options.Options(listen_host=self.mitm_host, listen_port=self.mitm_port, confdir=self.mitm_config_folder)

        self.dump_master = DumpMaster(
            opts,
            with_termlog=False,
            with_dumper=False,
        )
        self.dump_master.addons.add(ClientWebSocket())
        if self.enable_unlocker:
            self.dump_master.addons.add(ClientHTTP())
        from mhm.addons import WebSocketAddon as Unlocker
        self.dump_master.addons.add(Unlocker())
        await self.dump_master.run()
        return self.dump_master
    
    def shutdown_mitm(self):
        """ shutdown mitm proxy server and join thread"""        
        if self.dump_master:
            self.dump_master.shutdown()
            self.dump_master = None
        if self.mitm_thread:
            self.mitm_thread.join(timeout=5)
            self.mitm_thread = None
            
    def start_xmlrpc(self):
        """ Start xmlrpc server thread and browser thread"""
        
        # create the browser and action agent if playwright enabled
        if self.enable_playwright:
            self.browser = Browser(self.playwright_width, self.playwright_height, self.mitm_host, self.mitm_port)
        else:
            self.browser = None   
        
        # start liqi server
        self.liqi_server = LiqiServer(self.rpc_host, self.rpc_port, self.browser)
        self.rpc_thread = threading.Thread(
            name="XMLRPC Thread",
            target=self.liqi_server.start,
            daemon=True)
        self.rpc_thread.start()
        
        if self.browser:    # start browser
            self.browser.start_browser()        
        
        
    def shutdown_xmlrpc(self):
        """ Shutdown xmlrpc server, browser, and join thread"""
        if self.browser:
            self.browser.shutdown_browser()
            self.browser = None
        if self.liqi_server:
            self.liqi_server.shut_down()
            self.liqi_server = None
        if self.rpc_thread:
            self.rpc_thread.join(timeout=5)
            self.rpc_thread = None
            
            
if __name__ == '__main__':
    # Test MitmController
    print("Test MitmController")
    mitm_controller = MitmController()
    
    print("Start MITM")
    mitm_controller.start_mitm()
    print("Start XMLRPC")
    mitm_controller.start_xmlrpc()
    input("Press Enter to shut down...")

    print("Shutting down XMLRPC")
    mitm_controller.shutdown_xmlrpc()
    print("Shutting down MITM")
    mitm_controller.shutdown_mitm()
    print("Finished.")