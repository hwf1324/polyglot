# -*- coding: utf-8 -*-

"""
cdpBridge - Synchronous bridge to Chrome Headless via Chrome DevTools Protocol (CDP).

Thread-safe: all WebSocket operations are serialized via a lock, and each
evaluateSync call uses a unique, atomically-incremented message ID.
"""

import json
import os
import socket
import subprocess
import threading
import time
import urllib.request
import winreg
from typing import Any
from collections.abc import Callable

import globalVars
from logHandler import log

import websocket


DEBUG_PORT = 9222
USER_DATA_DIR = os.path.join(globalVars.appArgs.configPath, "polyglot_chrome_ai")


class CdpError(Exception):
	pass


def _isPortInUse(port: int) -> bool:
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
		return s.connect_ex(("127.0.0.1", port)) == 0


class CdpBridge:
	_instance = None
	_chromeProcess: subprocess.Popen | None = None
	_ws: websocket.WebSocket | None = None
	_wsLock = threading.Lock()
	_nextMsgId = 0
	_msgIdLock = threading.Lock()

	@classmethod
	def getInstance(cls) -> "CdpBridge":
		if cls._instance is None:
			cls._instance = CdpBridge()
		return cls._instance

	def _allocateMsgId(self) -> int:
		with self._msgIdLock:
			self._nextMsgId += 1
			return self._nextMsgId

	def _getChromePath(self) -> str:
		regPaths = [
			(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
			(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
		]
		for hkey, subKey in regPaths:
			try:
				with winreg.OpenKey(hkey, subKey) as key:
					path, _ = winreg.QueryValueEx(key, "")
					if os.path.exists(path):
						return path
			except FileNotFoundError:
				continue
		return ""

	def startBrowser(self) -> None:
		if self._chromeProcess and self._chromeProcess.poll() is None:
			return
		if _isPortInUse(DEBUG_PORT):
			log.info(f"Port {DEBUG_PORT} already in use, attempting to reuse existing Chrome instance.")
			try:
				wsUrl = self._getWebSocketUrl()
				if wsUrl:
					log.info("Reusing existing Chrome CDP endpoint.")
					return
			except CdpError:
				log.warning(f"Port {DEBUG_PORT} occupied but CDP unreachable.")
				raise CdpError(
					f"Port {DEBUG_PORT} is occupied by another process. "
					"Please close it or restart NVDA."
				)
		chromePath = self._getChromePath()
		if not chromePath:
			raise CdpError("Chrome not found. Please install Google Chrome.")
		os.makedirs(USER_DATA_DIR, exist_ok=True)
		log.info(f"Launching Chrome Headless: {chromePath}")
		try:
			self._chromeProcess = subprocess.Popen(
				[
					chromePath,
					"--headless=new",
					f"--remote-debugging-port={DEBUG_PORT}",
					f"--user-data-dir={USER_DATA_DIR}",
					"--remote-allow-origins=*",
					"--enable-features=TranslationAPI",
					"--disable-gpu",
					"--mute-audio",
					"--no-first-run",
					"--no-default-browser-check",
				],
				stdout=subprocess.DEVNULL,
				stderr=subprocess.DEVNULL,
			)
		except Exception as e:
			raise CdpError(f"Failed to start Chrome: {e}")

	def _getWebSocketUrl(self) -> str:
		url = f"http://127.0.0.1:{DEBUG_PORT}/json"
		for _ in range(20):
			try:
				req = urllib.request.Request(url)
				with urllib.request.urlopen(req, timeout=1) as response:
					data = json.loads(response.read().decode("utf-8"))
					pages = [t for t in data if t.get("type") == "page"]
					if pages:
						return pages[0]["webSocketDebuggerUrl"]
			except Exception:
				time.sleep(0.5)
		raise CdpError("Timeout waiting for Chrome CDP endpoint.")

	def ensureConnection(self) -> None:
		with self._wsLock:
			if self._ws and self._ws.connected:
				return
			self.startBrowser()
			wsUrl = self._getWebSocketUrl()
			log.info(f"Connecting to CDP WebSocket: {wsUrl}")
			try:
				self._ws = websocket.create_connection(wsUrl, timeout=300)
				enableId = self._allocateMsgId()
				self._ws.send(json.dumps({"id": enableId, "method": "Runtime.enable"}))
				self._ws.recv()
				log.debug("CDP Runtime domain enabled.")
			except Exception as e:
				raise CdpError(f"WebSocket connection failed: {e}")

	def evaluateSync(
		self,
		jsPayload: str,
		onConsoleLog: Callable[[str], None] | None = None,
	) -> dict[str, Any]:
		"""
		Thread-safe JS evaluation. Acquires the WebSocket lock for the
		entire send/recv cycle, ensuring only one evaluation runs at a time.
		Automatically retries once on stale connection errors (e.g. WinError 10053).
		"""
		for attempt in range(2):
			self.ensureConnection()
			msgId = self._allocateMsgId()
			cmd = {
				"id": msgId,
				"method": "Runtime.evaluate",
				"params": {
					"expression": jsPayload,
					"awaitPromise": True,
					"returnByValue": True,
					"userGesture": True,
				},
			}
			with self._wsLock:
				try:
					log.debug(f"CDP: evaluate id={msgId}, payload={len(jsPayload)} chars")
					self._ws.send(json.dumps(cmd))
					while True:
						responseStr = self._ws.recv()
						if not responseStr:
							raise CdpError("WebSocket closed unexpectedly.")
						response = json.loads(responseStr)
						if response.get("method") == "Runtime.consoleAPICalled":
							args = response.get("params", {}).get("args", [])
							for arg in args:
								logText = str(arg.get("value", ""))
								if onConsoleLog and logText:
									onConsoleLog(logText)
							continue
						if response.get("id") == msgId:
							if "error" in response:
								raise CdpError(f"CDP error: {response['error']}")
							resultValue = response.get("result", {}).get("result", {}).get("value", "{}")
							if isinstance(resultValue, dict):
								return resultValue
							if isinstance(resultValue, str):
								try:
									return json.loads(resultValue)
								except (json.JSONDecodeError, TypeError):
									return {"code": "PARSE_ERR", "raw": resultValue}
							return {"code": "PARSE_ERR", "raw": str(resultValue)}
				except websocket.WebSocketTimeoutException:
					raise CdpError("Timed out waiting for Chrome AI response.")
				except CdpError:
					raise
				except Exception as e:
					self._ws = None
					if attempt == 0:
						log.warning(f"WebSocket connection lost, reconnecting: {e}")
						continue
					raise CdpError(f"WebSocket error: {e}")

	def terminate(self) -> None:
		if self._ws:
			try:
				self._ws.close()
			except Exception:
				pass
			self._ws = None
		if self._chromeProcess:
			log.info("Terminating Chrome CDP process.")
			try:
				self._chromeProcess.terminate()
				self._chromeProcess.wait(timeout=5)
			except subprocess.TimeoutExpired:
				log.warning("Chrome did not exit gracefully, force killing.")
				self._chromeProcess.kill()
				self._chromeProcess.wait(timeout=3)
			except Exception:
				pass
			self._chromeProcess = None
