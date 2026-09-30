/* Solrise ERP - customer portal.
 *
 * Two behaviours and nothing else: the choice buttons, and filing the report.
 * There is no framework on purpose - `www/` files are served raw by the website
 * engine, so anything needing a build step would be one more thing to keep
 * working.
 *
 * Every handler is wrapped. A script error must never leave a customer unable to
 * report a broken pump: if this file fails entirely, the markup is still a form
 * with a submit button, and the worst case is a page reload.
 */
(function () {
	"use strict";

	/* Longest side, in px, after downscaling. A modern phone camera is 4000px
	   and several MB; at 1600px/JPEG this lands around 200-400 KB, which is the
	   difference between a report that sends at the pump and one that times out. */
	var PHOTO_MAX_EDGE = 1600;
	var PHOTO_QUALITY = 0.8;

	/* Kept in step with `api/portal.py:PHOTO_TYPES`. A file we would resize is
	   always JPEG by the time it is sent, so this list only matters for the
	   fallback path where the browser could not decode the image at all. */
	var SENDABLE_TYPES = ["image/jpeg", "image/jpg", "image/png", "image/webp"];

	function log(message, detail) {
		try {
			if (window.console && console.warn) {
				console.warn("[solrise-portal] " + message, detail || "");
			}
		} catch (e) {
			/* logging is cosmetic */
		}
	}

	function each(list, callback) {
		if (!list) {
			return;
		}
		for (var index = 0; index < list.length; index += 1) {
			callback(list[index], index);
		}
	}

	function by_id(id) {
		try {
			return document.getElementById(id);
		} catch (e) {
			return null;
		}
	}

	/* --------------------------------------------------------------- choices */

	/* The buttons are the interface; the hidden inputs are what is submitted.
	   Selection is mirrored into `aria-checked` as well as the class, so the
	   state is announced and not only seen. */
	function wire_choices(form) {
		each(form ? form.querySelectorAll("[data-group]") : null, function (button) {
			button.addEventListener("click", function () {
				var group = button.getAttribute("data-group");
				var scope = button.parentNode ? button.parentNode.querySelectorAll('[data-group="' + group + '"]') : null;

				each(scope, function (sibling) {
					sibling.classList.remove("is-selected");
					sibling.setAttribute("aria-checked", "false");
				});

				button.classList.add("is-selected");
				button.setAttribute("aria-checked", "true");

				var field = by_id("sl-" + group);
				if (field) {
					field.value = button.getAttribute("data-value");
				}
			});
		});
	}

	function value_of(id) {
		var field = by_id(id);
		return field && typeof field.value === "string" ? field.value : "";
	}

	/* ----------------------------------------------------------------- photo */

	function sendable_original(file, original) {
		/* The browser could not draw the file (HEIC on most phones). Sending it
		   unchanged only helps if the server accepts the type; otherwise the
		   photo is dropped and the report still goes. */
		return SENDABLE_TYPES.indexOf(String(file.type || "").toLowerCase()) >= 0 ? original : null;
	}

	function prepare_photo(file, done) {
		var reader = new FileReader();

		reader.onerror = function () {
			done(null);
		};

		reader.onload = function () {
			var original = reader.result;
			var image = new Image();

			image.onerror = function () {
				done(sendable_original(file, original));
			};

			image.onload = function () {
				try {
					var longest = Math.max(image.width, image.height) || 1;
					var scale = Math.min(1, PHOTO_MAX_EDGE / longest);
					var canvas = document.createElement("canvas");
					canvas.width = Math.max(1, Math.round(image.width * scale));
					canvas.height = Math.max(1, Math.round(image.height * scale));
					canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
					done(canvas.toDataURL("image/jpeg", PHOTO_QUALITY));
				} catch (e) {
					done(sendable_original(file, original));
				}
			};

			image.src = original;
		};

		reader.readAsDataURL(file);
	}

	function wire_photo() {
		var input = by_id("sl-photo");
		var preview = by_id("sl-photo-preview");
		var image = by_id("sl-photo-image");
		var clear = by_id("sl-photo-clear");
		var current = null;

		if (!input) {
			return { get: function () { return null; } };
		}

		input.addEventListener("change", function () {
			var file = input.files && input.files[0];
			if (!file) {
				return;
			}
			prepare_photo(file, function (data_url) {
				current = data_url;
				if (!data_url || !preview || !image) {
					return;
				}
				image.src = data_url;
				preview.hidden = false;
			});
		});

		if (clear) {
			clear.addEventListener("click", function () {
				current = null;
				if (image) {
					image.removeAttribute("src");
				}
				if (preview) {
					preview.hidden = true;
				}
				input.value = "";
			});
		}

		return {
			get: function () {
				return current;
			}
		};
	}

	/* ------------------------------------------------------------ the request */

	/* The same shape the chat widget uses, including the `settled` latch:
	   `error` can fire after `callback`, and answering once matters when the
	   answer is "your report was filed". */
	function call(method, args, on_done) {
		var settled = false;

		function finish(data) {
			if (settled) {
				return;
			}
			settled = true;
			on_done(data);
		}

		if (!window.frappe || typeof frappe.call !== "function") {
			log("frappe.call is unavailable");
			finish(null);
			return;
		}

		try {
			frappe.call({
				method: method,
				type: "POST",
				args: args || {},
				callback: function (r) {
					finish(r && !r.exc && r.message ? r.message : null);
				},
				error: function () {
					finish(null);
				}
			});
		} catch (e) {
			log("call to " + method + " failed", e);
			finish(null);
		}
	}

	/* --------------------------------------------------------------- feedback */

	function show_alert(message) {
		var alert = by_id("sl-alert");
		if (!alert) {
			return;
		}
		alert.textContent = message;
		alert.hidden = false;
		try {
			alert.scrollIntoView({ block: "center" });
		} catch (e) {
			/* scrolling is cosmetic */
		}
	}

	function hide_alert() {
		var alert = by_id("sl-alert");
		if (alert) {
			alert.hidden = true;
		}
	}

	function show_done(message) {
		var form = by_id("sl-report");
		var done = by_id("sl-done");
		var reference = by_id("sl-done-ref");
		var back = document.querySelector(".sl-back");

		if (back) {
			back.hidden = true;
		}
		if (form) {
			form.hidden = true;
		}
		if (done) {
			done.hidden = false;
		}
		if (reference) {
			/* The ticket id is the support team's handle on this report, so it is
			   shown - but as a traceable string, never as a required field. */
			var parts = [];
			if (message && message.name) {
				parts.push(message.name);
			}
			if (message && message.priority) {
				parts.push(message.priority);
			}
			reference.textContent = parts.join("  \u00b7  ");
		}
		try {
			if (done) {
				done.scrollIntoView({ block: "center" });
			}
		} catch (e) {
			/* scrolling is cosmetic */
		}
	}

	/* ------------------------------------------------------------ the submit */

	function wire_submit(form, photo) {
		var submit = by_id("sl-submit");
		var api = document.querySelector("[data-api]");

		if (!form || !api) {
			return;
		}

		/* The label is swapped for a word the template translates, because a button
		   that only greys out reads as "broken" rather than "working". */
		var label = submit ? submit.querySelector(".sl-submit__label") : null;
		var idle_label = label ? label.textContent : "";

		function busy(state) {
			if (!submit) {
				return;
			}
			submit.disabled = !!state;
			submit.setAttribute("aria-busy", state ? "true" : "false");
			if (label) {
				var sending = submit.getAttribute("data-sending");
				label.textContent = state && sending ? sending : idle_label;
			}
		}

		form.addEventListener("submit", function (event) {
			event.preventDefault();
			hide_alert();

			var args = {
				category: value_of("sl-category"),
				urgency: value_of("sl-urgency"),
				description: value_of("sl-description"),
				// Empty unless the user covers several stores, in which case the server
				// checks it against their own stores rather than trusting it.
				store: value_of("sl-store")
			};

			var attachment = photo.get();
			if (attachment) {
				args.attachment = attachment;
			}

			busy(true);
			call(api.getAttribute("data-api"), args, function (message) {
				busy(false);
				if (message && message.name) {
					show_done(message);
					return;
				}
				show_alert(
					"We could not send that. Please try again, or tell your supervisor."
				);
			});
		});
	}

	/* ----------------------------------------------------------------- start */

	/* A station login that strays onto a Desk URL. Frappe refuses the whole /app
	   tree to a `Website User` with a "Not Permitted" page - the correct answer, and
	   a dead end for someone who cannot read it. This file is already loaded on that
	   page (`web_include_js`), so ask the server where they belong and go there.
	   Only ever fires on a Frappe message/error page under /app, and the refusal
	   itself is untouched: this moves the user, it grants nothing. */
	function rescue_stray_customer() {
		try {
			if (!/^\/app(\/|$)/.test(window.location.pathname)) {
				return;
			}
			if (document.body.getAttribute("frappe-session-status") !== "logged-in") {
				return;
			}
			/* data-path is the renderer's name; "message" is the do-not-have-access,
			   not-found and error page. A working Desk route sets something else. */
			if (document.body.getAttribute("data-path") !== "message") {
				return;
			}
			window.fetch("/api/method/solrise_erp.api.portal.portal_home", {
				headers: { Accept: "application/json" },
				credentials: "same-origin"
			})
				.then(function (response) {
					return response.json();
				})
				.then(function (payload) {
					var home = payload && payload.message && payload.message.home;
					if (home) {
						log("sending a portal user to " + home);
						window.location.replace(home);
					}
				})
				.catch(function () {
					/* Leave the page as Frappe rendered it. */
				});
		} catch (e) {
			log("rescue failed", e);
		}
	}

	function on_ready() {
		try {
			rescue_stray_customer();
			var form = by_id("sl-report");
			if (!form) {
				return;
			}
			wire_choices(form);
			wire_submit(form, wire_photo());
		} catch (e) {
			log("init failed", e);
			/* The form stays usable: the browser will still submit it. */
		}
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", on_ready);
	} else {
		on_ready();
	}
})();
