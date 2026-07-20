import json

import addonHandler

from ..engine import BaseHttpEngine
from ...common import languages
from ...common.exceptions import ApiResponseError

addonHandler.initTranslation()


class LibreTranslate(BaseHttpEngine):
	id = "libreTranslate"
	name = _("LibreTranslate")

	@property
	def maxRequestLength(self) -> int:
		"""Use a conservative limit to avoid excessive latency on large texts."""
		return 5000

	@property
	def autoDetectCode(self) -> str:
		return "auto"

	@property
	def defaultTargetLanguage(self) -> str:
		return "zh-Hans"

	@property
	def reportsDetectedLanguage(self) -> bool:
		"""The API response includes detectedLanguage when source is 'auto'."""
		return True

	def getConfigSpec(self) -> list[dict]:
		spec = super().getConfigSpec()
		spec.extend(
			[
				{
					"id": "apiUrl",
					"label": _("API Server URL:"),
					"type": "text",
					"default": "https://libretranslate.com",
				},
				{
					"id": "apiKey",
					"label": _("API Key (optional):"),
					"type": "password",
					"default": "",
				},
			],
		)
		return spec

	def getSupportedLanguages(self) -> dict:
		supportedCodes = [
			"auto",
			"ar",
			"az",
			"bg",
			"bn",
			"ca",
			"cs",
			"da",
			"de",
			"el",
			"en",
			"eo",
			"es",
			"et",
			"eu",
			"fa",
			"fi",
			"fr",
			"ga",
			"gl",
			"he",
			"hi",
			"hu",
			"id",
			"it",
			"ja",
			"ko",
			"ky",
			"lt",
			"lv",
			"ms",
			"nb",
			"nl",
			"pl",
			"pt",
			"pt-BR",
			"ro",
			"ru",
			"sk",
			"sl",
			"sq",
			"sr",
			"sv",
			"sw",
			"th",
			"tl",
			"tr",
			"uk",
			"ur",
			"vi",
			"zh-Hans",
			"zh-Hant",
		]
		return languages.getLanguageDictForCodes(supportedCodes)

	def _buildRequestParams(self, text: str, langFrom: str, langTo: str, config: dict) -> dict:
		apiUrl = config.get("apiUrl", "https://libretranslate.com").rstrip("/")
		apiKey = config.get("apiKey", "").strip()

		payload = {
			"q": text,
			"source": langFrom,
			"target": langTo,
			"format": "text",
		}
		if apiKey:
			payload["api_key"] = apiKey

		return {
			"method": "POST",
			"url": f"{apiUrl}/translate",
			"headers": {"Content-Type": "application/json"},
			"data": json.dumps(payload).encode("utf-8"),
		}

	def _parseResponse(self, responseBody: str) -> dict:
		data = json.loads(responseBody)

		if "error" in data:
			raise ApiResponseError(data["error"])

		translation = data.get("translatedText", "")
		detectedInfo = data.get("detectedLanguage")
		langDetected = detectedInfo.get("language") if detectedInfo else None

		return {"translation": translation, "langDetected": langDetected}
