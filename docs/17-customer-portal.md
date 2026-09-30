# 17 - The customer portal

The screen a gas-station employee lands on: a page of big buttons, one of which —
for now — files a report with the support team.

Read [`docs/11-rbac.md`](11-rbac.md) section 2.1 for the permission model it
depends on, and [`docs/10-branding.md`](10-branding.md) for the branding it
inherits. This document is how it is built and how to extend it.

---

## 1. Who this is for, and why it looks like that

The audience is a station employee at one of ~50 sites. They are not
office users: they may not read comfortably, they are standing at a pump, they
are holding a phone in one hand, in daylight, and they are not interested in ERP.

That single fact drives every decision in this feature:

| Decision | Because |
|---|---|
| One screen, one obvious action | A wrong tap must cost nothing. There is no navigation to get lost in. |
| Icons carry meaning; labels confirm it | The picture is understood before the word is read. |
| Chrome removed (no navbar, no footer, no banner) | Every extra link is an exit into a system they cannot navigate. |
| Selection shown by border **and** tick **and** weight | Never colour alone: colour vision varies and sunlight flattens hue. |
| `min-height: 11rem` tiles, `1.5rem` labels | Thumb-sized targets, readable without zooming. |
| A photo is offered, text is optional | A picture of a broken pump is worth more than a written paragraph, and typing is the hardest task for this reader. |
| Success is a large green tick, not a toast | It must be unmistakable that the report was received. |

## 2. What it is made of

```
hooks.py                     role_home_page -> "start"  (the landing route)
                             web_include_css/js          (the portal assets)
portal/catalog.py            the buttons  <- add new buttons HERE
portal/identity.py           which Customer (station) the user is
portal/guard.py              the login gate every page passes through
api/portal.py                create_issue(): the only write path
www/start/                   the button page
www/start/report-issue/      the report form
public/css/solrise_portal.css
public/js/solrise_portal.js
install.py                   grants the Customer role its Issue permission
tests/test_portal_catalog.py structural checks, no bench needed
```

Two rules hold the design together:

1. **The buttons are data, not markup.** `portal/catalog.py` is a tuple of dicts
   and the templates loop over it. Adding a button is one entry, and the test
   refuses an entry whose route has no page.
2. **One write path.** The pages only read. Everything a customer can change goes
   through `api/portal.py`, server-side, from four inputs — so the form cannot set
   `status`, `customer`, the assignment, or any Service Level field.

## 3. How the landing page is chosen

`hooks.py` declares:

```python
role_home_page = {"Customer": ["start"]}
```

Frappe resolves a website user's destination in
`frappe/website/utils.py: get_home_page()` → `get_home_page_via_hooks()`, which
consults, in order: `Role.home_page` → `Portal Settings.default_portal_home` →
`website_user_home_page`/`role_home_page` → `Website Settings.home_page` → `"me"`.
So this map wins for exactly the `Customer` role and leaves Desk users on `/app`.

> **The value must be a list.** `get_home_page_via_hooks()` indexes it with
> `[-1]`. A bare string `"start"` resolves to the route `t`. The test pins this.

The result is cached per user (`frappe.cache.hget("home_page", user, ...)`), which
is why `install.apply_all()` and the deploy's cache clear both matter after a
change.

## 4. The permission this needs (and that ERPNext does not ship)

ERPNext grants permission on `Issue` to **`Support Team` and `Projects User`
only** — there is no `Customer` row at all. Without one, a portal user cannot
create or read a ticket, and the button has nowhere to go.

`install.ensure_portal_permissions()` therefore adds a `Custom DocPerm` on every
migrate if the row is missing:

| Field | Value |
|---|---|
| `read`, `write`, `create` | 1 |
| `if_owner` | **1** |
| `delete`, `report`, `export`, `import`, `share`, `print`, `email`, `submit`, `cancel`, `amend`, `select` | 0 |

`if_owner` is the whole security story: a customer sees their own reports and
nobody else's. It is deliberately *not* mirrored into
`permissions.issue_query_conditions()` — that hook narrows Support Agents, and
returning `None` for everyone else lets the role's own `if_owner` rule apply.

`File` needs no grant: Frappe's `File` DocType already includes the `All` role, so
photo uploads work. `Comment` is not granted, which is why the portal does **not**
show the reply thread — see section 8.

The function is idempotent: an existing row is left untouched, so a grant widened
by hand is not reset on the next migrate.

## 5. Adding a button later

Say a "Fuel price board" button is wanted:

1. **Write the page.** `www/start/fuel-price/index.html` (and `index.py` calling
   `guard.bootstrap(context, title=..., target="/start/fuel-price")`).
2. **Add the entry** to `TILES` in `portal/catalog.py`:

   ```python
   {
       "key": "fuel-price",
       "label": "Fuel prices",
       "caption": "Today's board",
       "icon": "\U0001F4C8",
       "route": "/start/fuel-price",
       "roles": (),
   },
   ```

3. **Run the test** — it fails if the route has no page, if the label is over 24
   characters, or if two tiles share a key:

   ```bash
   cd apps/solrise_erp && python3 -m unittest solrise_erp.tests.test_portal_catalog
   ```

Nothing else changes: the home page renders whatever the catalogue holds.

A button for a manager rather than a customer sets `"roles": ("Support Manager",)`
instead of `()`. An empty tuple means "everyone logged in", and a tile that names
roles is hidden — never shown — for a user who does not hold one.

**Adding a report category** is the same idea, one entry in `CATEGORIES`. Icons
are written as `\U...` escapes to keep the file ASCII. `DEFAULT_CATEGORY` must name
an entry that exists, or a request without a category degrades to the last entry.

**Where the current list comes from.** It is transcribed from the maintenance
category document: the 20 entries of its proposed main dropdown, in its order, plus
`Car Wash` (section 21 of its detailed spec, which its own summary table omits) and a
`Something else` escape hatch — 22 in total, and the document's own emoji for each.
Two entries of that table, `Gutters & Concrete` and `Intercom & Air Equipment`, were
dropped by the document's own dropdown and are therefore not offered; re-add them as
two more entries if that was an oversight rather than a decision.

22 choices is more than one phone screen can show well, so the category grid is
compacted (`.sl-choices--many`: shorter tiles, smaller icon) and takes a third column
above 30rem. The form is still about two phone screens tall, which is the honest cost
of 22 options. If that scroll proves to be a problem in the field, the alternative is
a two-step flow — four coarse groups (building / equipment / fuel / grounds), then the
category — at the price of one more decision for a user who may not read.

## 6. What the support team sees

Nothing new had to be built: the app already ships the `Issue` plumbing
(`fixtures/`), and the portal feeds it.

| Piece | Effect on a portal report |
|---|---|
| `Assignment Rule` "Solrise Support Routing" | Round-robin assign while `status == 'Open'` |
| `Notification` "Solrise New Ticket" | Emails the **Support Manager** role |
| `Service Level Agreement` on `Issue` | Starts the response clock, breach alerts |
| `Issue.via_customer_portal` | Set to 1, so the Desk can tell portal reports apart |
| `Issue.priority` | `Normal` → Medium, `Urgent` → High |

> **Check the Assignment Rule's user list before go-live.** It currently contains
> `Administrator` alone, so round-robin assigns to the built-in account. Add the
> real Support Agent users in **Assignment Rule → Solrise Support Routing →
> Users**, or assignment is a no-op and the notification is the only signal.

## 7. Deploying a change to it

The portal is part of the app, so it travels with the image:

1. `./scripts/publish-app.sh apps/solrise_erp` — pushes the app branch CI builds.
2. Trigger `build-image.yml` (a push to `main` touching `apps.json`,
   `infra/image/**` or the build scripts also does it).
3. Deploy on the host (`ansible-playbook site.yml --tags deploy`, or
   `SITE_ENV=aws make aws-up` from `/opt/solrise-erp`).

`bench migrate` runs on every deploy, which is what applies
`ensure_portal_permissions()` and reloads `hooks.py`. The deploy's cache clear is
what makes a changed landing route take effect for users who are already logged
in. New files under `public/` need no `bench build` — they are plain static files
under an existing `/assets/solrise_erp/` mount.

## 8. Known limits

- **The customer cannot see the reply thread.** The portal is a landing page and a
  form; replies arrive by e-mail, which is what `docs/13` section 12 describes.
  Adding an in-portal thread needs read/create on `Comment`/`Communication`, which
  is a deliberate decision, not an oversight.
- **iPhone HEIC photos are downscaled client-side, so they do not survive.** The
  browser cannot decode HEIC to a canvas, and the server accepts only
  JPEG/PNG/WebP. The report is still filed, without the photo. Fixing it properly
  means transcoding server-side.
- **The confirmation screen is the only feedback.** There is no "my reports" list
  yet, though `api.portal.my_issues()` already exists for it. The home page shows
  a count of open reports instead.
- **Labels are not translated.** They pass through `_()` (so a translation can be
  added), but no translations are shipped.
- **`web_include_css`/`web_include_js` load on every website page.** Both files are
  namespaced (`.sl-`, `#sl-report`) and the script returns immediately without a
  report form, so the cost is two small static files.
- **One button means one screen.** If the catalogue grows past about six tiles the
  single-column phone layout starts to scroll, which is when grouping becomes
  worth considering.

## 9. Verifying it

```bash
# the structural checks (no site needed)
cd apps/solrise_erp && python3 -m unittest solrise_erp.tests.test_portal_catalog

# the pages exist and demand a login (observed: 301 -> /login?redirect-to=...)
curl -s -o /dev/null -w '%{http_code}\n' https://<site>/start
curl -s -o /dev/null -w '%{http_code}\n' https://<site>/start/report-issue
curl -s -o /dev/null -w '%{http_code}\n' \
  https://<site>/assets/solrise_erp/css/solrise_portal.css                   # 200 text/css
```

Two details worth knowing when you read those responses:

* **Assets are served by nginx, not by the app server.** Asking gunicorn (port
  8000 in the container) for `/assets/...` returns Frappe's HTML 404, which looks
  like the "broken CSS" failure. Go through the frontend (8080 locally, 443 in
  production) or you will misdiagnose your own test.
* **`301`, not `302`.** `frappe.Redirect` is a permanent redirect; the browser
  will cache it. That is fine, and it is why the landing route is worth getting
  right before anyone visits the page.

Then, logged in as a portal user: the landing page is `/start`, it shows the one
button, filing a report lands on the tick, and the report appears in **Issue** with
`Customer`, `Priority` and `Via Customer Portal` set and the customer as its owner.
Log in as a *second* customer and confirm they cannot see the first one's report —
that is the `if_owner` grant, and it is the one thing worth re-checking after any
permission change.

### 9.1 What a local rehearsal actually showed

Run on a local `erp.localhost` site with the app installed by hand (see 9.2). The
full path was exercised, not just the templates:

| Step | Result |
|---|---|
| Login as a portal user | response carries `"home_page": "/start"` — Frappe confirming the landing route |
| `GET /start` | `200`, shows the tile and the station name |
| `GET /start/report-issue` | `200`, all 22 categories, both urgencies |
| `POST api.portal.create_issue` | `{"name": "ISS-2026-00001", "subject": "Fuel or pump - Station 12 - Houston", "priority": "High", "customer": "Station 12 - Houston", "photo": "/private/files/ISS-2026-00001-photo.png"}` |
| Filed ticket | `status=Open`, `via_customer_portal=1`, `owner=` the customer, `service_level_agreement=SLA-Issue-Standard`, `response_by` set, `_assign=["Administrator"]` (the routing rule fired) |
| Attachment | private `File` owned by the customer, attached to the Issue |
| Support Manager's list | shows `ISS-2026-00001` |
| A different station | empty list; `frappe.has_permission(..., doc=...)` is `False` |
| Unrelated guest | `/start` redirects to the login form |
| Press **Log out** on the confirmation panel | `301` → `/login`, and `/start` then bounces to the login form (session really ended) |

#### Two traps this shook out

1. **`Issue.priority` is a Link, not a Select** (ERPNext 15.121: Link to
   `Issue Priority`; older versions: a Select). `meta.get_options("priority")`
   returns the option list for a Select but the *target DocType* for a Link, so
   reading it as a list sets the priority to the literal `"Issue Priority"` and
   the insert dies with `LinkValidationError`. `api/portal.py:_safe_priority()`
   now branches on `fieldtype` and a test pins it (docs/17 section 4 is unrelated;
   this is `tests/test_portal_catalog.py::test_api_handles_both_priority_shapes`).
2. **`frappe.get_doc` does not enforce permissions.** Reading one Issue by name in
   a script "succeeded" for a user who should be denied — that is the ORM, and
   docs/11 section 4.2 says as much. Only the whitelist/API layer checks, which is
   why the honest test is `frappe.has_permission(..., doc=..., user=...)` (correctly
   `False` here) plus the list view (correctly empty).

Do not test isolation with `frappe.get_doc`; it will tell you there is a leak when
none exists.

### 9.2 Local prerequisites that are easy to miss

A local site that was brought up with `erpnext,hrms` only will fail the report
flow in two confusing ways until the deployment scripts have been run once:

* **Without `scripts/setup_erp.py`:** every Issue insert fails with
  `Holiday List Solrise Default Holidays not found`. The SLA fixture
  (`SLA-Issue-Standard`) points at a Holiday List that only that script creates,
  so the failure happens on *any* Issue insert, not just portal ones.
* **Without `scripts/roles_rbac.py`:** `Support Manager` has no `Issue` read
  permission (`get_role_permissions()` returns all zeros), so the manager's list
  raises `PermissionError`. The ticket is still filed; nobody can see it.

In production both are already satisfied (docs/16 section 5 records the
`setup_erp.py` run). On a **fresh** site the second one matters: the app's own
fixtures directory does not ship `custom_docperm.json` (it ships 11 of the 16
canonical fixture files), so the RBAC matrix arrives from `roles_rbac.py` in the
app-less path or from the existing database rows — not from the app. The portal's
own grant is deliberately created from code (`install.ensure_portal_permissions`)
so it does not depend on that.

### 9.3 Bringing the stack up without podman-compose

Where `podman-compose` cannot create pods (a host with no systemd user session —
every service in this compose file lives in one pod), the same stack runs on plain
`podman run` with host networking. This is what was used for the rehearsal above:

```bash
# database + redis, exposing 3306 / 6379 on the host
podman run -d --name sl_mariadb --network=host -e MYSQL_ROOT_PASSWORD=localdevonly \
  -v solrise_db_data:/var/lib/mysql docker.io/library/mariadb:11.8 \
  --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci \
  --skip-character-set-client-handshake
podman run -d --name sl_redis --network=host docker.io/library/redis:8.6-alpine

# backend (gunicorn on 8000) + frontend (nginx on 8080), both on host networking
podman run -d --name sl_backend --network=host -v solrise_sites:/home/frappe/frappe-bench/sites \
  localhost/solrise/erpnext:version-15
podman run -d --name sl_frontend --network=host -e BACKEND=127.0.0.1:8000 \
  -e SOCKETIO=127.0.0.1:9000 -e FRAPPE_SITE_NAME_HEADER=erp.localhost \
  -v solrise_sites:/home/frappe/frappe-bench/sites \
  localhost/solrise/erpnext:version-15 nginx-entrypoint.sh
```

Then two hand-fixes the compose file normally does for you:

* point the site at the loopback services, because host networking has no
  `mariadb`/`redis-cache` names to resolve — from inside the backend container:
  `sed -i 's/"mariadb"/"127.0.0.1"/; s/redis-cache/127.0.0.1/; s/redis-queue/127.0.0.1/' \
  /home/frappe/frappe-bench/sites/common_site_config.json`
  (the `configurator` service rewrites this file on the next compose up, so it is
  safe to edit; it is also why it cannot be edited from the host — the volume is
  owned by the container user);
* give every container the app under `/home/frappe/frappe-bench/apps/solrise_erp`,
  add it to `sites/apps.txt`, add a `.pth` in the venv pointing at it, and link it
  into `assets/`. **`nginx` needs the app too** — its filesystem is its own, so an
  asset symlink created only in the backend container leaves every
  `/assets/solrise_erp/*` request 404ing.

**Do not start a `dbus-daemon` on `/run/user/<uid>/bus` to fix the pod error.** It
makes things worse: `crun` then takes the sd-bus path and *every* `podman run`
fails with `sd-bus call: Process org.freedesktop.systemd1 exited with status 1`,
including ones that worked before. Remove the socket again if you tried it.

---

## 10. Logging out

A station phone is a shared device, so the end of the report flow is not "your
report was filed" — it is "the next person can log in". **Log out** therefore
appears on the confirmation panel (as the primary action, above the quieter
"Report another problem") and, quietly, at the foot of the button page. Both point
at `/start/logout` via `portal.guard.LOGOUT_URL`.

That route is a **website page, not a whitelisted method**, and the difference was
measured on a live site rather than assumed:

| Implementation | Response |
|---|---|
| a `@frappe.whitelist()` method (what was written first) | `301` with **no `Location` header** and a JSON body. The session *is* ended — the `sid` cookie is cleared — but the browser is stranded on a blank page |
| `/start/logout` (what ships) | `301`, `Location: /login` |

The API path loses the redirect because Frappe builds a JSON response for
`/api/method`, whereas a website route goes through
`frappe.local.flags.redirect_location` — the same mechanism
`portal.guard.require_login` uses for the login gate. `tests/test_portal_catalog.py`
pins the distinction by asserting the route carries no `@frappe.whitelist`
decorator, so nobody "tidies" it back into an API call.

Two platform options were read in the source and rejected:

* `/?cmd=web_logout`, which Frappe's own website navbar uses, ends on a
  "Logged Out" message page — a sentence to read, which is the one thing this
  audience cannot reliably do;
* `frappe.handler.logout` returns JSON and does not redirect at all, so it needs
  JavaScript to finish the job.

Logging out over `GET` is deliberate: the platform's own logout works that way,
and the worst a forged request achieves is ending a session. The route is tolerant
by design — a Guest, an expired session or a double press all land on the login
form rather than on an error.

---

## 11. A manager who covers several stores

The store list has managers who run more than one site — Tammy Hoeppner has four
(docs/18 section 5). `User.mobile_no` is unique, so one *person* gets one login, and
that login is linked from a Contact at **every** store they manage. Reporting
therefore cannot assume a single store, which shows up in two places:

* **`identity.stores()`** returns all of them, and the pages work from that list;
* **the report form asks which store** — but only when the list is longer than one,
  so the 21 single-store managers keep the original one-tap screen.

The store is then resolved **server-side, from the caller's own links**:

```
one store   -> used, whatever the form sent
several    -> the posted store must name one of theirs, or the request is refused
none       -> the report is still filed, without a customer
```

That last rule is the point. A posted store is a value from a browser, so
`_resolve_store()` never trusts it: filing a report against another station by
hand-crafting the request is refused, not honoured. Verified on a live site — a
four-store manager posting someone else's store gets `ValidationError: Please choose
which store this is about.` and no Issue is created.
