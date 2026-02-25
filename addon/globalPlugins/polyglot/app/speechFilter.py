# -*- coding: utf-8 -*-

import time
from typing import Any

import ui
from speech.extensions import filter_speechSequence

from ..common import cues
from .manager import TranslationManager


class SpeechFilter:
	# Annotate instance variables at the class level
	manager: TranslationManager
	lastSpokenText: str
	_isSpeakingTranslation: bool
	_suppressCapture: int
	_gracePeriodEnd: float

	def __init__(self, manager: TranslationManager) -> None:
		super().__init__()
		self.manager = manager
		self.lastSpokenText = ""
		self._isSpeakingTranslation = False
		self._suppressCapture = 0
		self._gracePeriodEnd = 0.0

	def register(self) -> None:
		"""Registers the speech filter and the cue suppression hook."""
		filter_speechSequence.register(self.onSpeechSequence)
		cues.registerSpeechHook(self.suppressNextCapture)

	def unregister(self) -> None:
		"""Unregisters the speech filter and the cue suppression hook."""
		_unused = filter_speechSequence.unregister(self.onSpeechSequence)
		cues.unregisterSpeechHook()

	def suppressNextCapture(self) -> None:
		"""Prevents the next speech sequence from being captured as spoken text."""
		self._suppressCapture += 1

	def setGracePeriod(self, durationMs: int = 300) -> None:
		"""Temporarily prevents speech from overwriting ``lastSpokenText``.

		Called when entering the command layer.  Releasing modifier keys
		(e.g. Shift from NVDA+Shift+T) may trigger IME or keyboard-layout
		switch notifications that would otherwise overwrite the text the
		user intends to translate.
		"""
		self._gracePeriodEnd = time.monotonic() + durationMs / 1000.0

	def onSpeechSequence(self, sequence: list[Any]) -> list[Any]:
		# Extract the text from the speech sequence.
		textToSave = " ".join([s for s in sequence if isinstance(s, str) and s.strip()])
		# Save the text unless suppression was requested by the cues module.
		# Suppressed speech is internal plugin messaging and should also
		# bypass auto-translate interception to avoid being swallowed.
		if textToSave:
			if self._suppressCapture > 0:
				self._suppressCapture -= 1
				return sequence
			elif time.monotonic() < self._gracePeriodEnd:
				# Inside the grace window; pass through without overwriting lastSpokenText.
				return sequence
			else:
				self.lastSpokenText = textToSave
		if not self.manager.isAutoTranslateEnabled:
			return sequence
		# To prevent translation loops, skip if the speech is already a translation result.
		if self._isSpeakingTranslation:
			self._isSpeakingTranslation = False
			return sequence
		# Trigger auto-translation if there is text.
		if textToSave:
			self.manager.requestTranslation(
				textToSave,
				isManual=False,
				showStatus=False,
				allowCopy=False,
				onSuccess=self._handleAutoTranslationResult,
			)
		# Block the original speech sequence; it will be replaced by the translation.
		return []

	def _handleAutoTranslationResult(self, translation: str) -> None:
		"""
		Callback for a successful auto-translation.
		Called by the TranslationManager on the main thread.
		"""
		# 1. Set a flag to prevent this result from being re-translated.
		self._isSpeakingTranslation = True
		# 2. Speak the translation.
		ui.message(translation)
