"""Best-effort UPnP IGD port mapping (pure Python: SSDP discovery + SOAP).

When enabled (Settings -> Network -> Use UPnP/NAT-PMP) and a torrent Listen Port
is set, this opens that port (TCP+UDP) on the router so inbound peer connections
reach the app. Everything is best-effort and swallows errors — a failed mapping
just means torrents fall back to outbound-only connectivity, never an error.

No external dependency; call map_port() from a daemon thread (it does network I/O).
"""
import re
import socket
import urllib.parse
import urllib.request

_SSDP_ADDR = ("239.255.255.250", 1900)
_ST = "urn:schemas-upnp-org:device:InternetGatewayDevice:1"


def _discover(timeout=2.0):
    """SSDP M-SEARCH -> the IGD's device-description LOCATION URL, or None."""
    msg = ("M-SEARCH * HTTP/1.1\r\n"
           "HOST: 239.255.255.250:1900\r\n"
           'MAN: "ssdp:discover"\r\n'
           "MX: 2\r\n"
           "ST: %s\r\n\r\n" % _ST)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.settimeout(timeout)
    try:
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


def _control_url(location):
    """From the device description XML, find the WAN(IP|PPP)Connection control
    URL + service type. Returns (control_url, service_type) or (None, None)."""
    try:
        xml = urllib.request.urlopen(location, timeout=4).read().decode("utf-8", "ignore")
    except Exception:
        return None, None
    for svc in re.findall(r"<service>(.*?)</service>", xml, re.S):
        if "WANIPConnection" in svc or "WANPPPConnection" in svc:
            t = re.search(r"<serviceType>(.*?)</serviceType>", svc, re.S)
            c = re.search(r"<controlURL>(.*?)</controlURL>", svc, re.S)
            if t and c:
                return urllib.parse.urljoin(location, c.group(1).strip()), t.group(1).strip()
    return None, None


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
        return True
    except Exception:
        return False


def map_port(port):
    """Map external:port -> this host:port (TCP+UDP) on the IGD. Returns True if
    at least one protocol mapped. Best-effort; safe to call from a daemon thread."""
    if not port:
        return False
    loc = _discover()
    if not loc:
        return False
    control_url, svc_type = _control_url(loc)
    if not control_url:
        return False
    ip = _local_ip()
    if not ip:
        return False
    ok = False
    for proto in ("TCP", "UDP"):
        if _add_mapping(control_url, svc_type, int(port), proto, ip):
            ok = True
    return ok
