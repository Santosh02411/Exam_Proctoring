"""IP Allowlisting / Geofencing — restrict where a proctored exam can be
taken from, two independent and optional layers:

  - IP allowlist (Organization.ip_allowlist, opted into per test via
    Test.enforce_ip_allowlist): a list of CIDR ranges — "this exam is only
    takeable from the campus network" style policies. Checked at exam
    start and on every heartbeat, so a student who starts on-campus and
    then switches to a VPN/mobile hotspot mid-exam still gets caught, not
    just one who tries to start off-network.

  - Geofence (Test.geofence_lat/lng/radius_km): a circular area checked
    against the browser's self-reported GPS position, captured once at
    exam start (see startGeoCheck in proctor.js). This is a *signal*, not
    a gate — permission can be denied, GPS accuracy varies by device, and
    a browser can lie about its own location — so unlike the IP check it
    never blocks the exam outright, only logs a proctoring event exactly
    like every other soft signal (screen recording declined, etc.).

Both funnel into the exact same _record_violation() pipeline as every
other proctoring signal, via event types "ip_out_of_range" and
"location_out_of_range" — which means the Customizable Warning System
(grace periods, warning limits, custom messages, per-test policy
overrides) already applies to them for free, and both show up in the
Behavior Timeline / Complete Exam Replay alongside everything else.
"""
import ipaddress
import json
import math

from flask import request


def get_client_ip():
    """Best-effort client IP. Uses request.remote_addr, which is correct
    out of the box for a direct connection but will report the *reverse
    proxy's* IP instead of the real client's if this app is deployed
    behind one (nginx, an ALB, Cloudflare, etc.) without also configuring
    Werkzeug's ProxyFix middleware to trust that proxy's X-Forwarded-For
    header. If IP allowlisting doesn't seem to be matching the addresses
    you expect in a proxied deployment, that's almost certainly why —
    ProxyFix needs the proxy's hop count, which is deployment-specific
    and shouldn't be guessed at here."""
    return request.remote_addr


def _parse_allowlist(raw):
    """Organization.ip_allowlist is stored as JSON text (a list of CIDR
    strings); tolerate it being blank, malformed, or holding an entry
    that isn't valid CIDR — one bad line in an admin's pasted list
    shouldn't take down every other entry or silently allow everything."""
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(entries, list):
        return []

    networks = []
    for entry in entries:
        try:
            networks.append(ipaddress.ip_network(str(entry).strip(), strict=False))
        except ValueError:
            continue
    return networks


def ip_allowed(ip_str, allowlist_raw):
    """True if ip_str falls inside any CIDR range in allowlist_raw, or if
    the allowlist is empty/unparseable (an org that hasn't set one up
    imposes no restriction — this is an opt-in feature, not a default-
    deny one, so a test enabling enforce_ip_allowlist against an org with
    no ranges configured yet fails open rather than locking everyone
    out by accident)."""
    networks = _parse_allowlist(allowlist_raw)
    if not networks:
        return True
    try:
        ip = ipaddress.ip_address(ip_str)
    except (ValueError, TypeError):
        return False
    return any(ip in net for net in networks)


def check_ip_allowlist(attempt):
    """Call at exam start and on every heartbeat/autosave for a test with
    enforce_ip_allowlist set. Returns True if the attempt was just
    terminated by this check (matching _record_violation's own return
    convention) so callers can short-circuit the same way they already do
    for every other terminating signal."""
    test = attempt.test
    if not test.enforce_ip_allowlist:
        return False
    org = test.organization
    if not org or not org.ip_allowlist:
        return False

    from app import proctoring  # local import: avoids a proctoring<->access_control import cycle
    ip = get_client_ip()
    if not ip_allowed(ip, org.ip_allowlist):
        return proctoring._record_violation(
            attempt, "ip_out_of_range", "violation",
            details=f"Request IP {ip} is outside {org.name}'s allowed range.",
            default_action="terminate",
        )
    return False


def _haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0088  # mean Earth radius, km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def check_geofence(attempt, lat, lng):
    """Called once from the /api/proctor/geo-check endpoint when the
    browser successfully reports a GPS position after the student grants
    location permission (see startGeoCheck in proctor.js). A denied
    permission or unsupported browser never reaches here at all — that's
    handled client-side as a soft "couldn't check" case, same tolerance
    as the screen-recording permission, not something this function needs
    to know about."""
    test = attempt.test
    if test.geofence_lat is None or test.geofence_lng is None or not test.geofence_radius_km:
        return False
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return False

    distance_km = _haversine_km(test.geofence_lat, test.geofence_lng, lat, lng)
    if distance_km > test.geofence_radius_km:
        from app import proctoring
        return proctoring._record_violation(
            attempt, "location_out_of_range", "violation",
            details=f"Reported location is {distance_km:.1f} km from the allowed area (limit {test.geofence_radius_km} km).",
            default_action="flag",
        )
    return False
