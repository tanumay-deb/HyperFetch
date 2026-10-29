"""Best-effort UPnP IGD port mapping (pure Python: SSDP discovery + SOAP).

When enabled (Settings -> Network -> Use UPnP) this opens the torrent listen
port (TCP+UDP) on the router so inbound peer connections reach the app.
Everything is best-effort and swallows errors — a failed mapping just means
torrents fall back to outbound-only connectivity, never an error. What the
router said is logged, once a start: it was silent, and on this machine it
had never worked (see _discover).

No external dependency; call map_port() from a daemon thread (it does network I/O).
"""
import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("hyperfetch.upnp")

_SSDP_ADDR = ("239.255.255.250", 1900)
_ST = "urn:schemas-upnp-org:device:InternetGatewayDevice:1"


def _discover(timeout=2.0):
    """SSDP M-SEARCH -> the IGD's device-description LOCATION URL, or None.

    Sent from the address that carries the default route (_local_ip). Left
    to itself Windows sends a multicast out of whichever adapter it likes,
    and with Tailscale installed that was not the one the router is on:
    measured 2026-10-22, the unbound search had no answer from any device,
    the same search from the Wi-Fi address 24 (3 of them the router's
    Internet gateway).
    """
    msg = ("M-SEARCH * HTTP/1.1\r\n"
           "HOST: 239.255.255.250:1900\r\n"
           'MAN: "ssdp:discover"\r\n'
           "MX: 2\r\n"
           "ST: %s\r\n\r\n" % _ST)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.settimeout(timeout)
    try:
        me = _local_ip()
        if me:
            try:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                             socket.inet_aton(me))
                s.bind((me, 0))
            except OSError as e:
                log.debug("UPnP search not tied to %s: %s", me, e)
        s.sendto(msg.encode(), _SSDP_ADDR)
        while True:
            data, _ = s.recvfrom(65507)
            m = re.search(r"LOCATION:\s*(\S+)", data.decode("utf-8", "ignore"), re.I)
            if m:
                return m.group(1)
    except (socket.timeout, OSError):
        return None
    finally:
        s.close()


def _control_urls(location):
    """From the device description XML, every WAN(IP|PPP)Connection control
    URL + service type, in the order listed: [(control_url, service_type)].

    All of them, as a router can list first one its line does not use:
    measured 2026-10-29 on the user's router (Airtel, Mediatek, libupnp on
    :5555), WANIPConnection comes first and answers every action -
    GetStatusInfo too - with UPnP error 401 "Invalid Action", and
    WANPPPConnection after it is the connected line. Only the first was ever
    asked. That router still opens nothing: its PPP service offers no
    AddPortMapping at all (its SCPD lists 14 actions, none for ports).
    """
    try:
        xml = urllib.request.urlopen(location, timeout=4).read().decode("utf-8", "ignore")
    except Exception:
        return []
    found = []
    for svc in re.findall(r"<service>(.*?)</service>", xml, re.S):
        if "WANIPConnection" in svc or "WANPPPConnection" in svc:
            t = re.search(r"<serviceType>(.*?)</serviceType>", svc, re.S)
            c = re.search(r"<controlURL>(.*?)</controlURL>", svc, re.S)
            if t and c:
                found.append((urllib.parse.urljoin(location, c.group(1).strip()),
                              t.group(1).strip()))
    return found


def _local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def _add_mapping(control_url, svc_type, port, proto, internal_ip):
    """"" when the router took the mapping, else what it said."""
    body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        '<u:AddPortMapping xmlns:u="%s">'
        '<NewRemoteHost></NewRemoteHost>'
        '<NewExternalPort>%d</NewExternalPort>'
        '<NewProtocol>%s</NewProtocol>'
        '<NewInternalPort>%d</NewInternalPort>'
        '<NewInternalClient>%s</NewInternalClient>'
        '<NewEnabled>1</NewEnabled>'
        '<NewPortMappingDescription>HyperFetch</NewPortMappingDescription>'
        '<NewLeaseDuration>0</NewLeaseDuration>'
        '</u:AddPortMapping></s:Body></s:Envelope>'
    ) % (svc_type, port, proto, port, internal_ip)
    req = urllib.request.Request(
        control_url, data=body.encode(),
        headers={"Content-Type": 'text/xml; charset="utf-8"',
                 "SOAPAction": '"%s#AddPortMapping"' % svc_type})
    try:
        urllib.request.urlopen(req, timeout=4).read()
        return ""
    except urllib.error.HTTPError as e:
        try:
            m = re.search(r"<errorDescription>([^<]*)<", e.read().decode("utf-8", "ignore"))
        except Exception:
            m = None
        return "HTTP %d%s" % (e.code, (" " + m.group(1).strip()) if m else "")
    except Exception as e:
        return type(e).__name__


def map_port(port):
    """Map external:port -> this host:port (TCP+UDP) on the IGD. Returns True if
    at least one protocol mapped. Best-effort; safe to call from a daemon thread."""
    if not port:
        return False
    loc = _discover()
    if not loc:
        log.info("UPnP: no router answered; torrent port %s is not opened", port)
        return False
    services = _control_urls(loc)
    if not services:
        log.info("UPnP: the router offers no port mapping; port %s is not opened", port)
        return False
    ip = _local_ip()
    if not ip:
        return False
    refused = []
    for control_url, svc_type in services:
        said = {proto: _add_mapping(control_url, svc_type, int(port), proto, ip)
                for proto in ("TCP", "UDP")}
        ok = [p for p, why in said.items() if not why]
        if ok:
            log.info("UPnP: the router opened torrent port %s (%s)", port, "+".join(ok))
            return True
        refused.append(", ".join("%s: %s" % kv for kv in said.items()))
    # 606 / HTTP 401-403: control switched off or locked; UPnP error 401
    # "Invalid Action": not offered there. "UPnP on" in a router's settings
    # does not always mean it maps ports (the user's does not).
    log.info("UPnP: the router refused to open torrent port %s (%s) - it does "
             "not let programs open ports; forward the port by hand", port,
             "; ".join(refused))
    return False
