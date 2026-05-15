# -*- coding: utf-8 -*-

"""
cdpBridge - Synchronous bridge to Chrome Headless via Chrome DevTools Protocol (CDP).

Thread-safe: all WebSocket operations are serialized via a lock, and each
evaluateSync call uses a unique, atomically-incremented message ID.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import winreg
from typing import Any
from collections.abc import Callable

import globalVars
from logHandler import log

import websocket


def _getUserDataDir() -> str:
	"""Returns the local Chrome profile directory used by Chrome AI."""
	localAppData = os.environ.get("LOCALAPPDATA")
	if localAppData:
		return os.path.join(localAppData, "Polyglot", "ChromeAI")
	return os.path.join(globalVars.appArgs.configPath, "polyglot_chrome_ai")


USER_DATA_DIR = _getUserDataDir()
DEVTOOLS_ACTIVE_PORT_FILE = os.path.join(USER_DATA_DIR, "DevToolsActivePort")
PAGE_FILE = os.path.join(USER_DATA_DIR, "chrome_ai.html")


class CdpError(Exception):
	"""Raised when Chrome DevTools Protocol communication fails."""

	pass


class CdpBridge:
	_instance = None
	_chromeProcess: subprocess.Popen | None = None
	_ws: websocket.WebSocket | None = None
	_wsLock = threading.Lock()
	_nextMsgId = 0
	_msgIdLock = threading.Lock()
	_debugPort: int | None = None
	_targetId: str | None = None
	_ownsBrowser = False

	@classmethod
	def getInstance(cls) -> "CdpBridge":
		"""Returns the singleton CDP bridge instance."""
		if cls._instance is None:
			cls._instance = CdpBridge()
		return cls._instance

	def _allocateMsgId(self) -> int:
		"""Returns a unique CDP command message ID."""
		with self._msgIdLock:
			self._nextMsgId += 1
			return self._nextMsgId

	def _getChromePath(self) -> str:
		"""Finds Chrome's executable path from the registry, common paths, or PATH."""
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
		candidates = [
			Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
			Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
			Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
		]
		for candidate in candidates:
			if candidate.exists():
				return str(candidate)
		fromPath = shutil.which("chrome.exe") or shutil.which("chrome")
		if fromPath:
			return fromPath
		return ""

	def startBrowser(self) -> None:
		"""Starts the managed headless Chrome instance if it is not already running."""
		if self._chromeProcess:
			if self._chromeProcess.poll() is None:
				return
			self._chromeProcess = None
			self._ownsBrowser = False
		self._debugPort = None
		self._targetId = None
		chromePath = self._getChromePath()
		if not chromePath:
			raise CdpError("Chrome not found. Please install Google Chrome.")
		os.makedirs(USER_DATA_DIR, exist_ok=True)
		pageUrl = self._preparePageUrl()
		if self._reuseExistingBrowser():
			return
		try:
			os.remove(DEVTOOLS_ACTIVE_PORT_FILE)
		except FileNotFoundError:
			pass
		except OSError:
			log.warning("Could not remove stale Chrome DevToolsActivePort file.", exc_info=True)
		log.info(f"Launching Chrome Headless: {chromePath}")
		try:
			self._chromeProcess = subprocess.Popen(
				[
					chromePath,
					"--headless=new",
					"--remote-debugging-port=0",
					f"--user-data-dir={USER_DATA_DIR}",
					"--remote-allow-origins=*",
					"--enable-features=TranslationAPI,LanguageDetectionAPI",
					"--disable-gpu",
					"--mute-audio",
					"--no-first-run",
					"--no-default-browser-check",
					pageUrl,
				],
				stdout=subprocess.DEVNULL,
				stderr=subprocess.DEVNULL,
			)
			self._ownsBrowser = True
		except Exception as e:
			self._ownsBrowser = False
			raise CdpError(f"Failed to start Chrome: {e}")

	def _readDevToolsActivePort(self) -> int | None:
		"""Reads the current Chrome DevTools port file if it exists."""
		try:
			with open(DEVTOOLS_ACTIVE_PORT_FILE, "r", encoding="utf-8") as portFile:
				firstLine = portFile.readline().strip()
				if firstLine:
					return int(firstLine)
		except (FileNotFoundError, ValueError, OSError):
			return None
		return None

	def _readJsonFromPort(self, port: int, path: str, method: str = "GET", timeout: float = 1) -> Any:
		"""Reads a JSON response from a Chrome debugging HTTP endpoint on a specific port."""
		url = f"http://127.0.0.1:{port}{path}"
		req = urllib.request.Request(url, method=method)
		with urllib.request.urlopen(req, timeout=timeout) as response:
			return json.loads(response.read().decode("utf-8"))

	def _reuseExistingBrowser(self) -> bool:
		"""Reuses an already-running managed Chrome debugging endpoint when it is still live."""
		port = self._readDevToolsActivePort()
		if port is None:
			return False
		for _ in range(10):
			try:
				versionInfo = self._readJsonFromPort(port, "/json/version", timeout=0.5)
				if not isinstance(versionInfo, dict):
					continue
				self._debugPort = port
				self._ownsBrowser = False
				log.info(f"Reusing existing Chrome CDP endpoint on port {port}.")
				return True
			except Exception:
				time.sleep(0.2)
		log.warning("Ignoring stale Chrome DevToolsActivePort; CDP endpoint was not reachable.")
		return False

	def _preparePageUrl(self) -> str:
		"""Creates a local secure-context page and returns its file URL."""
		html = '<!doctype html><meta charset="utf-8"><title>Polyglot Chrome AI</title>'
		with open(PAGE_FILE, "w", encoding="utf-8") as pageFile:
			pageFile.write(html)
		return Path(PAGE_FILE).resolve().as_uri()

	def _getDebugPort(self) -> int:
		"""Reads the ephemeral CDP port assigned to the managed Chrome process."""
		if self._debugPort is not None:
			return self._debugPort
		for _ in range(40):
			if self._chromeProcess and self._chromeProcess.poll() is not None:
				exitCode = self._chromeProcess.returncode
				self._chromeProcess = None
				self._ownsBrowser = False
				if exitCode == 21:
					if self._reuseExistingBrowser() and self._debugPort is not None:
						return self._debugPort
					raise CdpError("Chrome AI profile is already in use by another Chrome process.")
				raise CdpError(f"Chrome exited before CDP became available: {exitCode}")
			port = self._readDevToolsActivePort()
			if port is not None:
				self._debugPort = port
				return self._debugPort
			time.sleep(0.25)
		raise CdpError("Timeout waiting for Chrome DevToolsActivePort.")

	def _readJsonEndpoint(self, path: str, method: str = "GET") -> Any:
		"""Reads a JSON response from the managed Chrome debugging HTTP endpoint."""
		port = self._getDebugPort()
		return self._readJsonFromPort(port, path, method=method)

	def _findPageTargetInList(self, targets: Any, pageUrl: str) -> str | None:
		"""Finds the Chrome AI page target WebSocket URL in a CDP target list."""
		if not isinstance(targets, list):
			return None
		for target in targets:
			if not isinstance(target, dict):
				continue
			if target.get("type") != "page":
				continue
			wsUrl = target.get("webSocketDebuggerUrl")
			if not isinstance(wsUrl, str):
				continue
			targetId = target.get("id")
			if self._targetId and targetId and str(targetId) == self._targetId:
				return wsUrl
			if target.get("url") == pageUrl:
				self._targetId = str(targetId) if targetId else None
				return wsUrl
		return None

	def _findPageTarget(self, pageUrl: str) -> str | None:
		"""Finds an existing page target for the Chrome AI page if it is already open."""
		try:
			targets = self._readJsonEndpoint("/json/list")
		except Exception:
			return None
		return self._findPageTargetInList(targets, pageUrl)

	def _createPageTarget(self, pageUrl: str) -> str | None:
		"""Creates a page target and returns its WebSocket URL if available."""
		quotedUrl = urllib.parse.quote(pageUrl, safe="")
		try:
			target = self._readJsonEndpoint(f"/json/new?{quotedUrl}", method="PUT")
		except Exception:
			log.warning("Failed to create Chrome CDP page target.", exc_info=True)
			return None
		if isinstance(target, dict):
			targetId = target.get("id")
			self._targetId = str(targetId) if targetId else None
			wsUrl = target.get("webSocketDebuggerUrl")
			if isinstance(wsUrl, str):
				return wsUrl
		return None

	def _getWebSocketUrl(self) -> str:
		"""Returns a page target WebSocket URL from the managed Chrome process."""
		pageUrl = self._preparePageUrl()
		for _ in range(20):
			try:
				wsUrl = self._findPageTarget(pageUrl)
				if wsUrl:
					return wsUrl
				wsUrl = self._createPageTarget(pageUrl)
				if wsUrl:
					return wsUrl
			except Exception:
				log.debug("Chrome CDP page target is not ready yet.", exc_info=True)
			time.sleep(0.5)
		raise CdpError("Timeout waiting for Chrome CDP endpoint.")

	def _enableProtocolDomain(self, method: str) -> None:
		"""Enables a CDP protocol domain on the current WebSocket."""
		assert self._ws is not None
		msgId = self._allocateMsgId()
		self._ws.send(json.dumps({"id": msgId, "method": method}))
		while True:
			response = json.loads(self._ws.recv())
			if response.get("id") != msgId:
				continue
			if "error" in response:
				raise CdpError(f"CDP error: {response['error']}")
			return

	def _logPageDiagnostics(self) -> None:
		"""Logs readiness, security context, and Chrome AI API availability for the page target."""
		assert self._ws is not None
		expression = """
		JSON.stringify({
			readyState: document.readyState,
			isSecureContext: globalThis.isSecureContext,
			href: location.href,
			hasTranslator: typeof Translator !== 'undefined',
			hasLanguageDetector: typeof LanguageDetector !== 'undefined',
			userActivation: navigator.userActivation ? {
				isActive: navigator.userActivation.isActive,
				hasBeenActive: navigator.userActivation.hasBeenActive,
			} : null,
		})
		"""
		msgId = self._allocateMsgId()
		self._ws.send(
			json.dumps(
				{
					"id": msgId,
					"method": "Runtime.evaluate",
					"params": {
						"expression": expression,
						"awaitPromise": False,
						"returnByValue": True,
					},
				},
			),
		)
		while True:
			response = json.loads(self._ws.recv())
			if response.get("id") != msgId:
				continue
			resultValue = response.get("result", {}).get("result", {}).get("value", "{}")
			try:
				diagnostics = json.loads(resultValue) if isinstance(resultValue, str) else resultValue
			except (json.JSONDecodeError, TypeError):
				diagnostics = {"raw": resultValue}
			log.debug(f"Chrome AI page diagnostics: {diagnostics}")
			return

	def ensureConnection(self) -> None:
		"""Ensures that a Runtime-enabled WebSocket connection is ready."""
		with self._wsLock:
			if self._ws and self._ws.connected:
				return
			self.startBrowser()
			wsUrl = self._getWebSocketUrl()
			log.info(f"Connecting to CDP WebSocket: {wsUrl}")
			try:
				self._ws = websocket.create_connection(wsUrl, timeout=300)
				self._enableProtocolDomain("Runtime.enable")
				self._enableProtocolDomain("Page.enable")
				self._logPageDiagnostics()
				log.debug("CDP Runtime domain enabled.")
			except Exception as e:
				self._ws = None
				raise CdpError(f"WebSocket connection failed: {e}")

	def _formatExceptionDetails(self, exceptionDetails: dict[str, Any]) -> str:
		"""Formats CDP Runtime exception details for logs and user-facing errors."""
		text = exceptionDetails.get("text", "Runtime exception")
		exception = exceptionDetails.get("exception", {})
		description = exception.get("description") if isinstance(exception, dict) else None
		if description:
			return f"{text}: {description}"
		return str(text)

	def _getProcessCommandLine(self, processId: int) -> str | None:
		"""Returns a process command line using NVDA's WMI process lookup helper."""
		try:
			import appModuleHandler

			processInfo = appModuleHandler.getWmiProcessInfo(processId)
			commandLine = getattr(processInfo, "CommandLine", None)
		except Exception:
			log.debug(f"Could not read command line for Chrome process {processId}.", exc_info=True)
			return None
		return str(commandLine) if commandLine else None

	def _isManagedChromeProcess(self, processId: int) -> bool:
		"""Returns whether a process command line matches Polyglot's managed Chrome."""
		commandLine = self._getProcessCommandLine(processId)
		if commandLine is None:
			return False
		normalizedCommandLine = commandLine.replace("/", "\\").replace('"', "").casefold()
		normalizedUserDataDir = os.path.normpath(USER_DATA_DIR).replace("/", "\\").casefold()
		return (
			f"--user-data-dir={normalizedUserDataDir}" in normalizedCommandLine
			and "--headless" in normalizedCommandLine
			and "--remote-debugging-port=" in normalizedCommandLine
		)

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
							exceptionDetails = response.get("result", {}).get("exceptionDetails")
							if isinstance(exceptionDetails, dict):
								raise CdpError(self._formatExceptionDetails(exceptionDetails))
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
					try:
						self._ws.close()
					except Exception:
						pass
					self._ws = None
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
		"""Closes the CDP connection and terminates the managed Chrome process."""
		if self._ws:
			try:
				self._ws.close()
			except Exception:
				pass
			self._ws = None
		if self._chromeProcess:
			processId = self._chromeProcess.pid
			if not self._ownsBrowser:
				log.debug(f"Chrome CDP process {processId} was not started by this bridge; leaving it running.")
			elif self._chromeProcess.poll() is not None:
				log.debug(f"Chrome CDP process {processId} has already exited.")
			else:
				if not self._isManagedChromeProcess(processId):
					log.warning(
						f"Chrome process {processId} did not match Polyglot ownership checks; "
						"terminating because it was started by this bridge.",
					)
				log.info(f"Terminating Chrome CDP process {processId}.")
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
			self._debugPort = None
			self._targetId = None
			self._ownsBrowser = False
