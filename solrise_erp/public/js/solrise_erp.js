/* Solrise ERP - the client-side half of docs/10-branding.md.
 *
 * The server half lives in `branding.py` (the DocType values: System Settings,
 * Website Settings, Global Defaults, Company, Workspace labels) and in
 * `boot.py` (`boot_session`: `app_name`, the app-switcher entries and
 * `app_logo_url`). Neither can reach the strings the upstream app injects into
 * the Desk payload *after* `boot_session` has run, nor the plain text the Desk
 * renders from them, so this patch enforces the same replacement in the
 * browser: it runs on `frappe.ready` and again on `app_ready`.
 *
 * Rules: `textContent` only (never `innerHTML`), a single try/catch so a
 * branding surprise can never break the Desk, and idempotent - "Solrise"
 * contains no upstream token, so re-running is a no-op.
 */

(function () {
	"use strict";

	var BRAND = "Solrise";

	// Where the Desk renders the visible product name.
	var SELECTORS = [".app-name", ".navbar-brand", ".app-title", ".brand-name"];

	// `ERPNext` / `Frappe` anywhere in a candidate's text.
	var UPSTREAM = /ERPNext|Frappe/g;
	var UPSTREAM_TEST = /ERPNext|Frappe/;
	// A title that still *starts* with the upstream name has not been patched
	// yet; once it starts with "Solrise" it never matches again.
	var UPSTREAM_TITLE = /^(ERPNext|Frappe)/;

	/** True for a node inside the chat widget (its transcript may contain the
	 * upstream name in model output, and must be left exactly as delivered). */
	function inside_chat(node) {
		var current = node;
		while (current && current.nodeType === 1) {
			if (typeof current.className === "string" && current.className.indexOf("solrise-chat") !== -1) {
				return true;
			}
			current = current.parentNode;
		}
		return false;
	}

	/** A brand element often wraps the logo; setting its textContent outright
	 * would delete the image, so in that case only the text nodes change. */
	function has_media_child(node) {
		var i;
		var child;
		var tag;
		for (i = 0; i < node.childNodes.length; i++) {
			child = node.childNodes[i];
			if (child.nodeType !== 1) {
				continue;
			}
			tag = child.tagName ? String(child.tagName).toLowerCase() : "";
			if (tag === "img" || tag === "svg" || tag === "i" || tag === "use") {
				return true;
			}
		}
		return false;
	}

	function rewrite_text_nodes(node) {
		var i;
		var child;
		for (i = 0; i < node.childNodes.length; i++) {
			child = node.childNodes[i];
			if (child.nodeType === 3) {
				if (UPSTREAM_TEST.test(child.textContent || "")) {
					child.textContent = String(child.textContent).replace(UPSTREAM, BRAND);
				}
			} else if (child.nodeType === 1 && !inside_chat(child)) {
				rewrite_text_nodes(child);
			}
		}
	}

	function rewrite_element(node) {
		if (!node || node.nodeType !== 1 || inside_chat(node)) {
			return;
		}
		if (!UPSTREAM_TEST.test(node.textContent || "")) {
			return; // already branded, or an element that never shows the name
		}
		if (has_media_child(node)) {
			rewrite_text_nodes(node);
			return;
		}
		node.textContent = BRAND;
	}

	function patch_elements() {
		var nodes = document.querySelectorAll(SELECTORS.join(", "));
		var i;
		for (i = 0; i < nodes.length; i++) {
			rewrite_element(nodes[i]);
		}
	}

	function patch_title() {
		var title = document.title || "";
		if (!UPSTREAM_TITLE.test(title)) {
			return;
		}
		document.title = BRAND + " " + title;
	}

	function run() {
		try {
			patch_elements();
			patch_title();
		} catch (error) {
			if (window.console && typeof console.warn === "function") {
				console.warn("Solrise branding patch failed", error);
			}
		}
	}

	if (window.frappe && typeof frappe.ready === "function") {
		frappe.ready(run);
	} else if (typeof window.$ === "function") {
		window.$(document).ready(run);
	} else if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", run);
	} else {
		run();
	}

	// Desk re-renders parts of the shell after boot; `app_ready` is the hook for
	// that second pass.
	if (typeof window.$ === "function") {
		window.$(document).on("app_ready", run);
	}
})();
