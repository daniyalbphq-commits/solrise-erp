# Copyright (c) 2026, Solrise and contributors
# For license information, please see license.txt
"""Guardrails for the chat package, runnable without Frappe, bench or a site.

    cd apps/solrise_erp && python3 -m unittest solrise_erp.tests.test_chat_guardrails

What it enforces:

1. docs/12 section 7.3 - no file under ``chat/`` uses a banned data primitive, and
   ``ignore_permissions=True`` appears in ``chat/audit.py`` alone;
2. docs/12 section 5.2 - ``chat/nlp.py`` and ``chat/registry.py`` stay Frappe-free,
   which is what lets the phrase corpus run where there is no site;
3. no ``eval(`` or ``exec(`` anywhere under ``chat/`` - ``frappe.safe_eval`` is the
   sanctioned evaluator for metadata expressions, and matching it is explicitly
   not what this check does;
4. nothing under ``chat/`` imports ``solrise_erp.api``, which would be a cycle back
   into the HTTP surface that calls the package;
5. every Python file in the app still compiles - a syntax error is a failed
   ``bench migrate``, not a surprise at page load.

The checks glob for what they scan and skip what they do not find, because the
chat modules are written alongside this test rather than before it.
"""

import os
import py_compile
import re
import unittest

# docs/12 section 7.3. `frappe.get_list`, `frappe.get_doc`, `frappe.has_permission`
# and `frappe.get_meta` are the primitives the package may use instead.
BANNED = (
	"frappe.get_all(",
	"frappe.db.get_value(",
	"frappe.db.sql(",
	"frappe.set_user(",
)

# The single documented exception: the audit writer inserts its own rows, which no
# role is allowed to create, so the log stays append-only from the application.
IGNORE_PERMISSIONS = "ignore_permissions=True"
IGNORE_PERMISSIONS_FILE = "audit.py"
IGNORE_PERMISSIONS_RE = re.compile(r"ignore_permissions\s*=\s*True")

# `(?<![\w.])` keeps `frappe.safe_eval(` and `safe_exec`-style names out of the
# match: only the builtins themselves are forbidden.
EVAL_RE = re.compile(r"(?<![\w.])eval\s*\(")
EXEC_RE = re.compile(r"(?<![\w.])exec\s*\(")

FRAPPE_IMPORT_RE = re.compile(r"^\s*(?:import\s+frappe\b|from\s+frappe\b)", re.MULTILINE)
API_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+solrise_erp\.api\b", re.MULTILINE)

# The pure modules: everything else in `chat/` talks to Frappe by design.
FRAPPE_FREE = ("nlp.py", "registry.py")

SKIPPED_DIRECTORIES = ("__pycache__",)


def _here():
	"""This file's directory."""
	return os.path.dirname(os.path.abspath(__file__))


def _package_dir():
	"""The importable package directory, found by walking up from this file.

	The walk looks for the directory that holds ``chat/``; if the package has not
	been written that far yet, ``tests/``' own parent is the package by definition.
	"""
	directory = _here()
	while True:
		if os.path.isdir(os.path.join(directory, "chat")):
			return directory
		parent = os.path.dirname(directory)
		if parent == directory:
			return os.path.dirname(_here())
		directory = parent


def _app_dir():
	"""The app directory: the parent of the package (``apps/solrise_erp``)."""
	return os.path.dirname(_package_dir())


def _chat_dir():
	"""The chat package directory, or ``None`` while it does not exist."""
	path = os.path.join(_package_dir(), "chat")
	return path if os.path.isdir(path) else None


def _walk(root):
	"""Every file under ``root``, skipping bytecode caches, sorted for stable runs."""
	if not root or not os.path.isdir(root):
		return []
	paths = []
	for directory, subdirectories, filenames in os.walk(root):
		subdirectories[:] = [
			name for name in subdirectories if name not in SKIPPED_DIRECTORIES
		]
		for filename in filenames:
			if filename.endswith(".pyc"):
				continue
			paths.append(os.path.join(directory, filename))
	return sorted(paths)


def _label(path):
	"""A short path for a failure message, relative to the app directory."""
	try:
		return os.path.relpath(path, _app_dir())
	except ValueError:
		return path


def _lines(path):
	"""The file's lines; an undecodable byte is not what this test is about."""
	with open(path, encoding="utf-8", errors="replace") as handle:
		return handle.readlines()


def _offences(path, needles):
	"""``"12: frappe.db.sql("`` for every banned needle found, in file order."""
	found = []
	for number, line in enumerate(_lines(path), start=1):
		for needle in needles:
			if needle in line:
				found.append("{0}: {1}".format(number, needle))
	return found


def _matches(path, pattern):
	"""``"12: eval("`` for every match of a regex, in file order."""
	found = []
	for number, line in enumerate(_lines(path), start=1):
		match = pattern.search(line)
		if match:
			found.append("{0}: {1}".format(number, match.group(0).strip()))
	return found


def _message(path, rule, offences):
	"""A failure message that names the file, the rule and every offending line."""
	return "{0} violates {1}:\n  {2}".format(_label(path), rule, "\n  ".join(offences))


class TestChatGuardrails(unittest.TestCase):
	"""The rules a reviewer would otherwise have to catch by reading the diff."""

	def setUp(self):
		self.chat_dir = _chat_dir()
		self.chat_files = _walk(self.chat_dir)

	def _files(self):
		"""The files under ``chat/``, skipping the test when there are none yet."""
		if not self.chat_files:
			self.skipTest("no files under chat/ yet")
		return self.chat_files

	def _assert_clean(self, path, offences, rule):
		with self.subTest(file=_label(path)):
			self.assertEqual([], offences, _message(path, rule, offences))

	def test_no_banned_data_primitives(self):
		"""docs/12 section 7.3: every read and write goes through Frappe's own gate."""
		for path in self._files():
			self._assert_clean(
				path,
				_offences(path, BANNED),
				"the banned data primitives of docs/12 section 7.3",
			)

	def test_ignore_permissions_only_in_audit(self):
		"""The audit writer is the one file allowed to bypass DocType permissions."""
		for path in self._files():
			if os.path.basename(path) == IGNORE_PERMISSIONS_FILE:
				continue
			self._assert_clean(
				path,
				_matches(path, IGNORE_PERMISSIONS_RE),
				"the no-bypass rule outside {0}".format(IGNORE_PERMISSIONS_FILE),
			)

	def test_pure_modules_stay_frappe_free(self):
		"""`nlp.py` and `registry.py` must import without Frappe (docs/12 5.2)."""
		for name in FRAPPE_FREE:
			path = os.path.join(self.chat_dir, name)
			if not os.path.isfile(path):
				continue
			self._assert_clean(
				path,
				_matches(path, FRAPPE_IMPORT_RE),
				"the pure-module rule: no Frappe import in {0}".format(name),
			)

	def test_no_eval_or_exec(self):
		"""Metadata expressions go through `frappe.safe_eval`, never the builtins."""
		for path in self._files():
			for pattern in (EVAL_RE, EXEC_RE):
				self._assert_clean(
					path,
					_matches(path, pattern),
					"the no-eval rule (use frappe.safe_eval)",
				)

	def test_no_api_import_cycle(self):
		"""Nothing under `chat/` may import the HTTP surface that calls it."""
		for path in self._files():
			self._assert_clean(
				path,
				_matches(path, API_IMPORT_RE),
				"the no-import-cycle rule (solrise_erp.api)",
			)

	def test_every_python_file_compiles(self):
		"""A syntax error anywhere in the app is a migrate that cannot run."""
		paths = [path for path in _walk(_app_dir()) if path.endswith(".py")]
		if not paths:
			self.skipTest("no Python files found to compile")
		for path in paths:
			with self.subTest(file=_label(path)):
				try:
					py_compile.compile(path, doraise=True)
				except py_compile.PyCompileError as error:
					self.fail("does not compile: {0}".format(error))


if __name__ == "__main__":
	unittest.main()
