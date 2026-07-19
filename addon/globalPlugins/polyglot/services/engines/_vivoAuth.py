# -*- coding: utf-8 -*-

# Copyright (C) 2025, Cary-rowen from NVDACN
#
# This module handles the generation of authentication headers required
# for the VIVO API. It interfaces with the NVDACN API to securely
# obtain a signature without exposing the private APP_KEY on the client-side.

import json
import random
import string
import time
import urllib.parse

import addonHandler
from logHandler import log

from ...common import network
from ...common.exceptions import (
	AuthenticationError,
	NetworkConnectionError,
	ResponseParsingError,
)

addonHandler.initTranslation()

__all__ = ["genSignHeaders"]

NVDACN_API_URL = "https://nvdacn.com/api/"
VIVO_APP_ID = "3046775094"
AUTH_REQUEST_TIMEOUT = 3  # Seconds for a single authentication request attempt


def _genNonce(length: int = 8) -> str:
	"""Generates a random alphanumeric string of a given length."""
	chars = string.ascii_lowercase + string.digits
	return "".join(random.choice(chars) for _ in range(length))


def _genCanonicalQueryString(params: dict) -> str:
	"""Creates a sorted, URL-encoded query string for signature consistency."""
	if not params:
		return ""
	sortedParams = sorted(params.items())
	return "&".join(f"{urllib.parse.quote(k)}={urllib.parse.quote(str(v))}" for k, v in sortedParams)


@network.retryOnNetworkError(attempts=3, delay=0.5, backoff=2)
def _fetchSignatureFromService(nvdacnUser: str, nvdacnPass: str, signingStringBytes: bytes) -> str:
	"""
	Fetches the signature from the NVDACN API using the robust network module.
	This function benefits from the centralized retry logic.
	"""
	apiParams = {"user": nvdacnUser, "pass": nvdacnPass, "name": "vivo", "action": "signature"}
	url = f"{NVDACN_API_URL}?{urllib.parse.urlencode(apiParams)}"

	log.debug("Requesting Vivo signature from NVDACN API for user: %s", nvdacnUser)

	try:
		responseBody = network.sendRequest(
			method="POST",
			url=url,
			data=signingStringBytes,
			timeout=AUTH_REQUEST_TIMEOUT,
		)

		result = json.loads(responseBody)

		if result.get("code") == 200 and "data" in result:
			log.info("Successfully fetched Vivo signature for user %s.", nvdacnUser)
			return result["data"]
		else:
			errorMessage = result.get("data", "Unknown API error")
			log.error(
				"NVDACN signature API returned a business error for user %s: %s (Code: %s)",
				nvdacnUser,
				errorMessage,
				result.get("code"),
			)
			raise AuthenticationError(f"NVDACN API Error: {errorMessage} (Code: {result.get('code')})")

	except NetworkConnectionError as e:
		log.error(
			"A network error occurred while fetching Vivo signature for user: %s.",
			nvdacnUser,
			exc_info=True,
		)
		raise AuthenticationError(_("Could not connect to the authentication server.")) from e
	except (json.JSONDecodeError, KeyError, TypeError) as e:
		log.error(
			"Invalid response from NVDACN API: %s",
			responseBody[:200],
			exc_info=True,
		)
		raise ResponseParsingError(_("Invalid response from the authentication server.")) from e


def genSignHeaders(nvdacnUser: str, nvdacnPass: str, method: str, uri: str, query: dict) -> dict:
	"""
	Generates the complete set of authentication headers for the VIVO API.

	This is the main public function of the module.
	"""
	method = str(method).upper()
	timestamp = str(int(time.time()))
	nonce = _genNonce()
	# Step 1: Prepare the canonical string to be signed.
	canonicalQueryString = _genCanonicalQueryString(query)
	signedHeadersString = (
		f"x-ai-gateway-app-id:{VIVO_APP_ID}\nx-ai-gateway-timestamp:{timestamp}\nx-ai-gateway-nonce:{nonce}"
	)
	signingString = (
		f"{method}\n{uri}\n{canonicalQueryString}\n{VIVO_APP_ID}\n{timestamp}\n{signedHeadersString}"
	)
	signingStringBytes = signingString.encode("utf-8")
	# Step 2: Fetch the signature from the remote service.
	signature = _fetchSignatureFromService(nvdacnUser, nvdacnPass, signingStringBytes)
	# Step 3: Assemble the final headers dictionary.
	return {
		"X-AI-GATEWAY-APP-ID": VIVO_APP_ID,
		"X-AI-GATEWAY-TIMESTAMP": timestamp,
		"X-AI-GATEWAY-NONCE": nonce,
		"X-AI-GATEWAY-SIGNED-HEADERS": "x-ai-gateway-app-id;x-ai-gateway-timestamp;x-ai-gateway-nonce",
		"X-AI-GATEWAY-SIGNATURE": signature,
	}
