/* Solrise ERP - universal chat widget, shared by Desk and Portal.
 *
 * SECURITY RULE - NON-NEGOTIABLE
 * ---------------------------------------------------------------------------
 * Every string that arrives from the server (the assistant's reply, the menu,
 * the question / confirm / link payloads, error strings) and every string the
 * user types is UNTRUSTED. It reaches the DOM exclusively through
 * `document.createTextNode(...)` or `node.textContent = ...`. This file must
 * never call `.html()` / `.append(...)` with markup and must never assign
 * `innerHTML`, with or without interpolation: the whole point of the server
 * design (docs/12, "Output handling (LLM02/LLM05)") is that model output cannot
 * inject markup into the Desk or the Portal.
 * Attributes that carry server text (`title`, `value`) are set with
 * `setAttribute` / DOM properties, which never parse HTML either.
 *
 * Loaded by `hooks.py` twice: `app_include_js` (Desk) and `web_include_js`
 * (Portal). Plain ES5-compatible vanilla JS - no build step, no imports, no
 * dependency beyond what both channels already provide (`frappe`, `$`).
 *
 * Server contract (solrise_erp.api.chat): see docs/12-phase5.
 * ========================================================================== */

(function () {
	"use strict";

	// Guard against a double include (Desk + Portal both pointing at this file).
	if (window.solrise_chat) {
		return;
	}

	var RPC_BOOTSTRAP = "solrise_erp.api.chat.bootstrap";
	var RPC_TURN = "solrise_erp.api.chat.turn";

	// Bounded mount retry: the Desk toolbar is built asynchronously after the
	// boot payload arrives. 20 x 250ms = 5s, then we stop and fall back to the
	// floating button. Deliberately not an unbounded interval.
	var MOUNT_TRIES = 20;
	var MOUNT_DELAY = 250;

	// The two control actions the server understands in `action`.
	var ACTION_CONFIRM = "__confirm__";
	var ACTION_CANCEL = "__cancel__";

	var TEXT = {
		trigger: "Ask Solrise",
		open: "Open chat",
		send: "Send",
		placeholder: "Ask Solrise...",
		yes: "Yes, proceed",
		no: "No",
		working: "Working...",
		disabled: "The chat assistant is currently disabled.",
		greeting: "Hi - what would you like to do?",
		unreachable: "I could not reach the assistant. Please try again.",
		required: "Please enter a value.",
		close: "Close"
	};

	var COLOR = {
		ink: "#111827",
		paper: "#ffffff",
		assistant: "#f3f4f6",
		border: "#e5e7eb",
		muted: "#6b7280"
	};

	var FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";

	var state = {
		mounted: false,
		booted: false, // bootstrap has painted the greeting at least once
		busy: false,
		session_id: null,
		channel: null, // "Desk" | "Portal", from bootstrap, else inferred
		menu: [],
		settings: {},
		payload: {}, // answers collected for the pending intent
		pending_node: null
	};

	var ui = null; // { host, root, transcript, input, send }
	var mount_timer = null;

	/* ---------------------------------------------------------------- utils */

	function warn(message, error) {
		try {
			if (window.console && typeof console.warn === "function") {
				console.warn("Solrise chat: " + message, error || "");
			}
		} catch (e) {
			/* a logger must never be the thing that breaks the page */
		}
	}

	function str(value) {
		return typeof value === "string" ? value : "";
	}

	/** First argument that is a non-empty string, else "". */
	function pick() {
		var i;
		for (i = 0; i < arguments.length; i++) {
			if (str(arguments[i])) {
				return arguments[i];
			}
		}
		return "";
	}

	function is_array(value) {
		return Object.prototype.toString.call(value) === "[object Array]";
	}

	function is_filled_array(value) {
		return is_array(value) && value.length > 0;
	}

	function has_own(object, key) {
		return Object.prototype.hasOwnProperty.call(object, key);
	}

	/** Create an element; `style` is a plain object of CSS primitives. */
	function make(tag, class_name, style) {
		var node = document.createElement(tag);
		if (class_name) {
			node.className = class_name;
		}
		if (style) {
			var key;
			for (key in style) {
				if (has_own(style, key)) {
					try {
						node.style[key] = style[key];
					} catch (e) {
						/* an unsupported property is not worth failing over */
					}
				}
			}
		}
		return node;
	}

	/** Text always enters the DOM here - never as markup. */
	function put_text(node, value) {
		node.appendChild(document.createTextNode(str(value)));
		return node;
	}

	function make_button(label, primary, class_name) {
		var button = make("button", class_name || "solrise-chat-btn", {
			padding: "5px 10px",
			marginRight: "6px",
			marginTop: "2px",
			borderRadius: "6px",
			fontSize: "12px",
			fontFamily: FONT,
			cursor: "pointer",
			border: "1px solid " + (primary ? COLOR.ink : COLOR.border),
			background: primary ? COLOR.ink : COLOR.paper,
			color: primary ? COLOR.paper : COLOR.ink
		});
		button.type = "button";
		put_text(button, label);
		return button;
	}

	/* ------------------------------------------------------------ transport */

	function call(method, args, on_done) {
		var settled = false;

		function finish(data) {
			if (settled) {
				return; // `error` can fire after `callback`; only answer once
			}
			settled = true;
			on_done(data);
		}

		if (!window.frappe || typeof frappe.call !== "function") {
			warn("frappe.call is unavailable; is the page a Frappe page?");
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
			warn("call to " + method + " failed", e);
			finish(null);
		}
	}

	/* ------------------------------------------------------------ transcript */

	function append_node(node) {
		if (!ui || !ui.transcript) {
			return;
		}
		ui.transcript.appendChild(node);
		try {
			ui.transcript.scrollTop = ui.transcript.scrollHeight;
		} catch (e) {
			/* scrolling is cosmetic */
		}
	}

	function add_turn(role, value) {
		var text = str(value);
		if (!text) {
			return;
		}
		var is_user = role === "user";
		var row = make("div", "solrise-chat-turn solrise-chat-turn-" + (is_user ? "user" : "assistant"), {
			display: "flex",
			margin: "6px 0",
			justifyContent: is_user ? "flex-end" : "flex-start"
		});
		var bubble = make("div", "solrise-chat-bubble", {
			padding: "7px 10px",
			borderRadius: "10px",
			fontSize: "13px",
			lineHeight: "1.45",
			fontFamily: FONT,
			maxWidth: "85%",
			whiteSpace: "pre-wrap",
			wordBreak: "break-word",
			background: is_user ? COLOR.ink : COLOR.assistant,
			color: is_user ? COLOR.paper : COLOR.ink,
			border: "1px solid " + (is_user ? COLOR.ink : COLOR.border)
		});
		put_text(bubble, text);
		row.appendChild(bubble);
		append_node(row);
	}

	function add_action_row(node) {
		var row = make("div", "solrise-chat-actions", {
			display: "flex",
			flexWrap: "wrap",
			alignItems: "center",
			margin: "6px 0",
			rowGap: "4px"
		});
		row.appendChild(node);
		append_node(row);
		return row;
	}

	function add_hint(row, message) {
		var hint = make("div", "solrise-chat-hint", {
			fontSize: "12px",
			color: COLOR.muted,
			width: "100%",
			marginTop: "2px"
		});
		put_text(hint, message);
		row.appendChild(hint);
	}

	function clear_transcript() {
		if (!ui || !ui.transcript) {
			return;
		}
		while (ui.transcript.firstChild) {
			ui.transcript.removeChild(ui.transcript.firstChild);
		}
		state.pending_node = null;
	}

	/* ---------------------------------------------------------- server turns */

	function add_menu(menu) {
		if (!is_filled_array(menu)) {
			return;
		}
		var row = make("div", "solrise-chat-menu", {
			margin: "4px 0 2px"
		});
		var i;
		for (i = 0; i < menu.length; i++) {
			(function (item) {
				var button = make_button(item.label, false, "solrise-chat-btn solrise-chat-menu-btn");
				if (item.hint) {
					button.setAttribute("title", item.hint); // attribute, never markup
				}
				button.onclick = function () {
					if (state.busy) {
						return;
					}
					add_turn("user", item.label);
					send_turn({ action: item.key });
				};
				row.appendChild(button);
			})(menu[i]);
		}
		append_node(row);
	}

	function add_link(link) {
		if (!link || typeof link !== "object") {
			return;
		}
		var name = str(link.name);
		var route = str(link.route);
		if (!name || !route) {
			return;
		}
		var button = make_button(name, false, "solrise-chat-btn solrise-chat-link");
		button.onclick = function () {
			navigate(route);
		};
		add_action_row(button);
	}

	function add_confirm(confirm) {
		var summary = confirm && typeof confirm === "object" ? str(confirm.summary) : "";
		var row = make("div", "solrise-chat-confirm", {
			margin: "6px 0",
			display: "flex",
			flexWrap: "wrap",
			alignItems: "center"
		});
		if (summary) {
			var line = make("div", "solrise-chat-confirm-summary", {
				width: "100%",
				fontSize: "13px",
				fontWeight: "600",
				fontFamily: FONT,
				marginBottom: "4px",
				color: COLOR.ink
			});
			put_text(line, summary);
			row.appendChild(line);
		}

		var yes = make_button(TEXT.yes, true, "solrise-chat-btn solrise-chat-btn-primary");
		var no = make_button(TEXT.no, false, "solrise-chat-btn solrise-chat-btn-secondary");

		function answer(button, label, action) {
			if (button.disabled) {
				return;
			}
			yes.disabled = true;
			no.disabled = true;
			add_turn("user", label);
			send_turn({ action: action });
		}

		yes.onclick = function () {
			answer(yes, TEXT.yes, ACTION_CONFIRM);
		};
		no.onclick = function () {
			answer(no, TEXT.no, ACTION_CANCEL);
		};

		row.appendChild(yes);
		row.appendChild(no);
		append_node(row);
	}

	/** One input control per `question.fieldtype`. */
	function build_input(fieldname, fieldtype, options, label) {
		var base = {
			padding: "6px 8px",
			border: "1px solid " + COLOR.border,
			borderRadius: "6px",
			fontSize: "13px",
			fontFamily: FONT,
			color: COLOR.ink,
			background: COLOR.paper,
			boxSizing: "border-box",
			width: "100%",
			flex: "1 1 auto",
			minWidth: "0"
		};
		var input;

		if (fieldtype === "Select") {
			input = make("select", "solrise-chat-input", base);
			var blank = make("option", null, null);
			blank.value = "";
			put_text(blank, label);
			input.appendChild(blank);

			var parts = options.split("\n");
			var i;
			for (i = 0; i < parts.length; i++) {
				var value = str(parts[i]).replace(/^\s+|\s+$/g, "");
				if (!value) {
					continue;
				}
				var option = make("option", null, null);
				option.value = value;
				put_text(option, value);
				input.appendChild(option);
			}
		} else if (fieldtype === "Date") {
			input = make("input", "solrise-chat-input", base);
			input.type = "date";
		} else if (fieldtype === "Int" || fieldtype === "Float") {
			input = make("input", "solrise-chat-input", base);
			input.type = "number";
			input.step = fieldtype === "Int" ? "1" : "any";
		} else {
			input = make("input", "solrise-chat-input", base);
			input.type = "text";
		}

		input.setAttribute("name", fieldname);
		input.setAttribute("autocomplete", "off");
		return input;
	}

	function add_question(question) {
		var fieldname = question && typeof question === "object" ? str(question.fieldname) : "";
		if (!fieldname) {
			add_turn("assistant", TEXT.unreachable);
			state.payload = {};
			return;
		}

		var fieldtype = pick(question.fieldtype, "Data");
		var label = pick(question.label, fieldname);
		var row = make("div", "solrise-chat-question", {
			margin: "6px 0",
			display: "flex",
			flexWrap: "wrap",
			alignItems: "center"
		});

		var caption = make("div", "solrise-chat-question-label", {
			width: "100%",
			fontSize: "12px",
			fontWeight: "600",
			fontFamily: FONT,
			color: COLOR.muted,
			marginBottom: "4px"
		});
		put_text(caption, label);
		row.appendChild(caption);

		var input = build_input(fieldname, fieldtype, str(question.options), label);
		if (fieldtype !== "Select") {
			input.setAttribute("placeholder", label);
		}
		var send = make_button(TEXT.send, true, "solrise-chat-btn solrise-chat-btn-primary");
		send.style.marginLeft = "6px";

		function submit() {
			if (input.disabled) {
				return;
			}
			var value = str(input.value).replace(/^\s+|\s+$/g, "");
			if (!value) {
				add_hint(row, TEXT.required);
				focus_node(input);
				return;
			}
			input.disabled = true;
			send.disabled = true;
			add_turn("user", label + ": " + value);
			// The collected answers travel in `payload`; a question answer is
			// neither a free-text `message` nor a menu `action`.
			state.payload[fieldname] = value;
			send_turn({});
		}

		send.onclick = submit;
		input.onkeydown = function (event) {
			if (event && (event.keyCode === 13 || event.key === "Enter")) {
				if (event.preventDefault) {
					event.preventDefault();
				}
				submit();
				return false;
			}
		};

		row.appendChild(input);
		row.appendChild(send);
		append_node(row);
		focus_node(input);
	}

	function handle_turn(data) {
		if (!data || typeof data !== "object") {
			add_turn("assistant", TEXT.unreachable);
			state.payload = {};
			return;
		}
		remember(data);

		if (data.ok === false) {
			add_turn("assistant", pick(data.error, data.reply, TEXT.unreachable));
			finish_turn(data);
			return;
		}

		var reply = str(data.reply);
		if (reply) {
			add_turn("assistant", reply);
		}

		// A pending question or confirmation keeps `payload` (the answers so far)
		// alive; anything else ends the intent.
		if (data.kind === "question") {
			add_question(data.question);
			return;
		}
		if (data.kind === "confirm") {
			add_confirm(data.confirm);
			return;
		}
		if (data.kind === "result") {
			add_link(data.link);
		}
		finish_turn(data);
	}

	function finish_turn(data) {
		state.payload = {};
		var menu = data ? data.menu : null;
		if (is_filled_array(menu)) {
			state.menu = clean_menu(menu);
			add_menu(state.menu);
		}
	}

	function remember(data) {
		if (str(data.session_id)) {
			state.session_id = data.session_id;
		}
		if (data.channel === "Desk" || data.channel === "Portal") {
			state.channel = data.channel;
		}
		if (data.settings && typeof data.settings === "object") {
			state.settings = data.settings;
		}
	}

	function clean_menu(menu) {
		var out = [];
		var i;
		for (i = 0; i < menu.length; i++) {
			var item = menu[i];
			if (!item || typeof item !== "object") {
				continue;
			}
			if (!str(item.key) || !str(item.label)) {
				continue; // a menu entry we could not act on is worse than none
			}
			out.push({
				key: str(item.key),
				label: str(item.label),
				hint: str(item.hint)
			});
		}
		return out;
	}

	/* --------------------------------------------------------- request cycle */

	function send_turn(request) {
		var body = { payload: JSON.stringify(state.payload) };
		// Exactly one of `message` / `action` per turn.
		if (request && str(request.message)) {
			body.message = request.message;
		}
		if (request && str(request.action)) {
			body.action = request.action;
		}
		if (state.session_id) {
			body.session_id = state.session_id;
		}

		set_busy(true);
		call(RPC_TURN, body, function (data) {
			set_busy(false);
			try {
				handle_turn(data);
			} catch (e) {
				warn("rendering the assistant turn failed", e);
				add_turn("assistant", TEXT.unreachable);
			}
		});
	}

	function set_busy(busy) {
		state.busy = !!busy;
		if (ui) {
			try {
				ui.input.disabled = state.busy;
				ui.send.disabled = state.busy;
			} catch (e) {
				/* the compose box is optional */
			}
		}
		if (state.busy) {
			show_pending();
		} else {
			hide_pending();
		}
	}

	function show_pending() {
		if (!ui || state.pending_node) {
			return;
		}
		var row = make("div", "solrise-chat-pending", {
			margin: "6px 0",
			fontSize: "12px",
			fontFamily: FONT,
			color: COLOR.muted
		});
		put_text(row, TEXT.working);
		state.pending_node = row;
		append_node(row);
	}

	function hide_pending() {
		if (state.pending_node && state.pending_node.parentNode) {
			state.pending_node.parentNode.removeChild(state.pending_node);
		}
		state.pending_node = null;
	}

	/* ------------------------------------------------------- routing a link */

	function desk_channel() {
		if (state.channel === "Desk") {
			return true;
		}
		if (state.channel === "Portal") {
			return false;
		}
		// bootstrap has not answered yet - fall back to an ambient Desk check.
		try {
			if (window.frappe && frappe.ui && frappe.ui.toolbar) {
				return true;
			}
			if (window.frappe && frappe.app) {
				return true;
			}
		} catch (e) {
			/* treat as Portal */
		}
		return false;
	}

	function navigate(route) {
		var target = str(route);
		if (!target) {
			return;
		}
		try {
			if (desk_channel() && window.frappe && typeof frappe.set_route === "function") {
				// `frappe.set_route` takes a route, not a URL path.
				frappe.set_route(target.charAt(0) === "/" ? target.slice(1) : target);
				hide_chat();
				return;
			}
			window.location.href = target;
		} catch (e) {
			warn("could not open " + target, e);
			try {
				window.location.href = target;
			} catch (inner) {
				/* nothing left to try */
			}
		}
	}

	/* ----------------------------------------------------------- the host UI */

	function dialog_body(dialog) {
		if (!dialog) {
			return null;
		}
		if (dialog.body && dialog.body.nodeType === 1) {
			return dialog.body;
		}
		if (dialog.$body && dialog.$body[0] && dialog.$body[0].nodeType === 1) {
			return dialog.$body[0];
		}
		if (dialog.$wrapper && typeof dialog.$wrapper.find === "function") {
			var found = dialog.$wrapper.find(".modal-body");
			if (found && found[0]) {
				return found[0];
			}
		}
		return null;
	}

	function create_dialog_host() {
		var dialog = null;
		try {
			if (!window.frappe || !frappe.ui || typeof frappe.ui.Dialog !== "function") {
				return null;
			}
			dialog = new frappe.ui.Dialog({ title: TEXT.trigger });
			var body = dialog_body(dialog);
			if (!body) {
				return null;
			}
			return {
				kind: "dialog",
				body: body,
				show: function () {
					dialog.show();
				},
				hide: function () {
					dialog.hide();
				}
			};
		} catch (e) {
			warn("could not build a Frappe dialog; using a plain panel", e);
			return null;
		}
	}

	function create_panel_host() {
		var overlay = make("div", "solrise-chat-overlay", {
			position: "fixed",
			left: "0",
			top: "0",
			right: "0",
			bottom: "0",
			background: "rgba(17, 24, 39, 0.45)",
			zIndex: "1040",
			display: "none"
		});
		var panel = make("div", "solrise-chat-panel", {
			position: "fixed",
			right: "16px",
			bottom: "16px",
			width: "360px",
			maxWidth: "92vw",
			background: COLOR.paper,
			color: COLOR.ink,
			borderRadius: "10px",
			boxShadow: "0 12px 32px rgba(0, 0, 0, 0.28)",
			display: "flex",
			flexDirection: "column",
			overflow: "hidden",
			zIndex: "1041",
			fontFamily: FONT
		});

		var header = make("div", "solrise-chat-header", {
			display: "flex",
			alignItems: "center",
			justifyContent: "space-between",
			padding: "8px 10px",
			background: COLOR.ink,
			color: COLOR.paper,
			fontSize: "13px",
			fontWeight: "600",
			fontFamily: FONT
		});
		put_text(header, TEXT.trigger);

		var close = make("button", "solrise-chat-close", {
			background: "transparent",
			border: "0",
			color: COLOR.paper,
			fontSize: "16px",
			lineHeight: "1",
			cursor: "pointer",
			padding: "0 2px"
		});
		close.type = "button";
		close.setAttribute("aria-label", TEXT.close);
		put_text(close, "\u00d7");
		header.appendChild(close);
		panel.appendChild(header);

		var body = make("div", "solrise-chat-body", { padding: "10px" });
		panel.appendChild(body);
		overlay.appendChild(panel);

		var host = {
			kind: "panel",
			body: body,
			show: function () {
				overlay.style.display = "block";
			},
			hide: function () {
				overlay.style.display = "none";
			}
		};

		close.onclick = function () {
			host.hide();
		};
		overlay.onclick = function (event) {
			if (event && event.target === overlay) {
				host.hide();
			}
		};

		document.body.appendChild(overlay);
		return host;
	}

	/** Build the widget DOM (once) inside whichever host is available. */
	function render() {
		var host = create_dialog_host() || create_panel_host();

		var root = make("div", "solrise-chat", { fontFamily: FONT, color: COLOR.ink });
		var transcript = make("div", "solrise-chat-transcript", {
			height: "320px",
			maxHeight: "320px",
			overflowY: "auto",
			padding: "8px 10px",
			background: COLOR.paper,
			border: "1px solid " + COLOR.border,
			borderRadius: "8px",
			boxSizing: "border-box"
		});

		var compose = make("div", "solrise-chat-compose", {
			display: "flex",
			alignItems: "center",
			marginTop: "8px"
		});
		var input = make("input", "solrise-chat-compose-input", {
			flex: "1 1 auto",
			minWidth: "0",
			padding: "6px 8px",
			border: "1px solid " + COLOR.border,
			borderRadius: "6px",
			fontSize: "13px",
			fontFamily: FONT,
			color: COLOR.ink,
			background: COLOR.paper,
			boxSizing: "border-box"
		});
		input.type = "text";
		input.setAttribute("autocomplete", "off");
		input.setAttribute("placeholder", TEXT.placeholder);

		var send = make_button(TEXT.send, true, "solrise-chat-btn solrise-chat-btn-primary solrise-chat-compose-send");
		send.style.marginLeft = "6px";
		send.style.marginRight = "0";

		compose.appendChild(input);
		compose.appendChild(send);
		root.appendChild(transcript);
		root.appendChild(compose);
		host.body.appendChild(root);

		ui = {
			host: host,
			root: root,
			transcript: transcript,
			input: input,
			send: send
		};

		// The compose box is the free-text half of every turn.
		send.onclick = function () {
			submit_text();
		};
		input.onkeydown = function (event) {
			if (event && (event.keyCode === 13 || event.key === "Enter")) {
				if (event.preventDefault) {
					event.preventDefault();
				}
				submit_text();
				return false;
			}
		};

		return ui;
	}

	function ensure_ui() {
		if (ui && ui.root && ui.root.parentNode) {
			return ui;
		}
		return render();
	}

	function submit_text() {
		if (!ui || state.busy) {
			return;
		}
		var value = str(ui.input.value).replace(/^\s+|\s+$/g, "");
		if (!value) {
			return;
		}
		ui.input.value = "";
		add_turn("user", value);
		send_turn({ message: value });
	}

	function focus_node(node) {
		if (!node || typeof node.focus !== "function") {
			return;
		}
		try {
			node.focus();
		} catch (e) {
			/* focus is cosmetic */
		}
	}

	function hide_chat() {
		if (ui && ui.host && typeof ui.host.hide === "function") {
			try {
				ui.host.hide();
			} catch (e) {
				warn("could not hide the chat", e);
			}
		}
	}

	/* -------------------------------------------------------------- the flow */

	/** open -> bootstrap() -> greeting + menu as a transcript. */
	function bootstrap_into_chat() {
		set_busy(true);
		call(RPC_BOOTSTRAP, {}, function (data) {
			set_busy(false);

			if (!data || typeof data !== "object") {
				add_turn("assistant", TEXT.unreachable);
				return;
			}
			remember(data);

			if (data.ok === false) {
				add_turn("assistant", pick(data.error, TEXT.unreachable));
				return;
			}
			if (data.enabled === false) {
				if (!state.booted) {
					add_turn("assistant", pick(data.greeting, TEXT.disabled));
				}
				state.booted = true;
				return;
			}

			if (is_filled_array(data.menu)) {
				state.menu = clean_menu(data.menu);
			}
			if (!state.booted) {
				// First open: paint the greeting and the quick actions.
				state.booted = true;
				clear_transcript();
				add_turn("assistant", pick(data.greeting, TEXT.greeting));
				add_menu(state.menu);
			}
			// A later open keeps the running transcript.
		});
	}

	function open_chat() {
		try {
			ensure_ui();
			ui.host.show();
			focus_node(ui.input);
			bootstrap_into_chat();
		} catch (e) {
			warn("could not open the chat", e);
		}
	}

	/* ------------------------------------------------------------ the trigger */

	function desk_detected() {
		try {
			if (!window.frappe) {
				return false;
			}
			// Both of these are created by Desk-only bundles; the Portal's
			// website theme never defines them.
			if (frappe.ui && frappe.ui.toolbar) {
				return true;
			}
			if (frappe.app) {
				return true;
			}
		} catch (e) {
			/* treat as Portal */
		}
		return false;
	}

	function mount_navbar() {
		var item = document.querySelector(".solrise-chat-trigger");
		if (item) {
			return true;
		}

		try {
			var toolbar = window.frappe && frappe.ui ? frappe.ui.toolbar : null;
			if (toolbar && typeof toolbar.add_dropdown_button === "function") {
				var before = document.querySelectorAll(".navbar .dropdown").length;
				try {
					toolbar.add_dropdown_button(TEXT.trigger, [
						{
							label: TEXT.open,
							action: function () {
								open_chat();
							}
						}
					]);
				} catch (e) {
					warn("add_dropdown_button failed; appending a plain navbar item", e);
				}
				if (document.querySelectorAll(".navbar .dropdown").length > before) {
					return true;
				}
			}
		} catch (e) {
			warn("toolbar lookup failed", e);
		}

		var nav =
			document.querySelector("#navbarSupportedContent ul.navbar-nav") ||
			document.querySelector("ul.navbar-nav");
		if (!nav) {
			return false; // the navbar is not in the DOM yet - retry
		}

		var list_item = make("li", "nav-item solrise-chat-trigger");
		var link = make("a", "nav-link solrise-chat-trigger-link");
		link.setAttribute("href", "#");
		link.setAttribute("role", "button");
		put_text(link, TEXT.trigger);
		link.onclick = function (event) {
			if (event && event.preventDefault) {
				event.preventDefault();
			}
			open_chat();
			return false;
		};
		list_item.appendChild(link);
		nav.appendChild(list_item);
		return true;
	}

	function mount_floating() {
		try {
			if (document.querySelector(".solrise-chat-fab")) {
				return true;
			}
			// The Portal has no navbar (and no toolbar); this is the only entry
			// point there. Inline styles only - no stylesheet is loaded for it.
			var button = make("button", "solrise-chat-fab", {
				position: "fixed",
				right: "16px",
				bottom: "16px",
				zIndex: "1000",
				padding: "10px 16px",
				border: "0",
				borderRadius: "999px",
				background: COLOR.ink,
				color: COLOR.paper,
				fontSize: "13px",
				fontWeight: "600",
				fontFamily: FONT,
				cursor: "pointer",
				boxShadow: "0 6px 18px rgba(0, 0, 0, 0.25)"
			});
			button.type = "button";
			put_text(button, TEXT.trigger);
			button.onclick = function () {
				open_chat();
			};
			document.body.appendChild(button);
			return true;
		} catch (e) {
			warn("could not mount the floating trigger", e);
			return false;
		}
	}

	function mount_trigger() {
		if (state.mounted) {
			return true;
		}
		if (desk_detected()) {
			// Desk: the navbar entry, retried until the toolbar has rendered.
			if (mount_navbar()) {
				state.mounted = true;
				return true;
			}
			return false;
		}
		if (mount_floating()) {
			state.mounted = true;
			return true;
		}
		return false;
	}

	function mount_with_retry() {
		var tries = 0;

		function attempt() {
			mount_timer = null;
			var mounted = false;
			try {
				mounted = mount_trigger();
			} catch (e) {
				warn("mounting the trigger failed", e);
			}
			if (mounted) {
				return;
			}
			tries += 1;
			if (tries >= MOUNT_TRIES) {
				// Bounded: give up on the navbar and use the floating button,
				// which works in both channels.
				try {
					state.mounted = mount_floating();
				} catch (e) {
					warn("the fallback trigger failed too", e);
				}
				return;
			}
			mount_timer = window.setTimeout(attempt, MOUNT_DELAY);
		}

		attempt();
	}

	function start() {
		try {
			if (window.frappe && typeof frappe.ready === "function") {
				frappe.ready(function () {
					mount_with_retry();
				});
			} else if (typeof window.$ === "function") {
				window.$(document).ready(function () {
					mount_with_retry();
				});
			} else if (document.readyState === "loading") {
				document.addEventListener("DOMContentLoaded", mount_with_retry);
			} else {
				mount_with_retry();
			}
		} catch (e) {
			// A widget problem must never break the Desk or the Portal.
			warn("initialisation failed", e);
		}
	}

	// Manual testing / deep links: `solrise_chat.open()` in the browser console.
	window.solrise_chat = { open: open_chat };

	try {
		start();
	} catch (e) {
		warn("start failed", e);
	}
})();
