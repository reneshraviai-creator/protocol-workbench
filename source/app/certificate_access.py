"""Pinned-peer OPC UA certificate checks and local managed certificate inventory."""
import base64
from datetime import datetime,timezone
import ipaddress
from pathlib import Path
from urllib.parse import urlparse
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from asyncua.crypto.validator import CertificateValidator,CertificateValidatorOptions as O
from aiohttp import web


def load(data):
    try:return x509.load_pem_x509_certificate(data) if b'-----BEGIN CERTIFICATE-----' in data else x509.load_der_x509_certificate(data)
    except ValueError:raise ValueError('Invalid X.509 certificate (PEM or DER required)') from None


def describe(data):
    cert=load(data);now=datetime.now(timezone.utc)
    return {'subject':cert.subject.rfc4514_string(),'issuer':cert.issuer.rfc4514_string(),'sha256':cert.fingerprint(hashes.SHA256()).hex(),'not_before':cert.not_valid_before_utc.isoformat(),'not_after':cert.not_valid_after_utc.isoformat(),'valid_now':cert.not_valid_before_utc<=now<=cert.not_valid_after_utc}


def client_identity(config):
    cert=load(Path(config['certificate']).read_bytes())
    try:uris=cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.UniformResourceIdentifier)
    except x509.ExtensionNotFound:uris=[]
    configured=config.get('application_uri')
    if configured and configured in uris:return configured
    if not configured and len(uris)==1:return uris[0]
    raise ValueError('Client certificate requires one application URI SAN (or a matching configured application_uri)')


def validator(config):
    client=load(Path(config['certificate']).read_bytes());peer=load(Path(config['server_certificate']).read_bytes())
    keydata=Path(config['private_key']).read_bytes()
    try:key=serialization.load_pem_private_key(keydata,password=None) if b'-----BEGIN' in keydata else serialization.load_der_private_key(keydata,password=None)
    except (ValueError,TypeError):raise ValueError('Invalid or password-protected client private key') from None
    if key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)!=client.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo):raise ValueError('Client certificate and private key do not match')
    if not describe(Path(config['certificate']).read_bytes())['valid_now']:raise ValueError('Client certificate is expired or not yet valid')
    expected=peer.fingerprint(hashes.SHA256());host=urlparse(config['url']).hostname
    async def check(cert,application):
        if cert.fingerprint(hashes.SHA256())!=expected:raise ValueError('Server certificate differs from the explicitly trusted certificate')
        await CertificateValidator(O.TIME_RANGE|O.URI|O.KEY_USAGE|O.EXT_KEY_USAGE|O.PEER_SERVER).validate(cert,application)
        try:san=cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        except x509.ExtensionNotFound:raise ValueError('Server certificate lacks Subject Alternative Name') from None
        try:match=ipaddress.ip_address(host) in san.get_values_for_type(x509.IPAddress)
        except ValueError:match=host.lower().rstrip('.') in [v.lower().rstrip('.') for v in san.get_values_for_type(x509.DNSName)]
        if not match:raise ValueError('Server certificate does not identify the endpoint hostname/IP; exact SAN match required')
    return check


async def inventory(request):
    folder=request.app['certificate_folder'];rows=[]
    if folder.exists():
        for file in sorted(folder.iterdir()):
            if not file.is_file() or file.suffix.lower()=='.key':continue
            try:rows.append({'name':file.name,**describe(file.read_bytes())})
            except ValueError:continue
    return web.json_response(rows)


def validate_upload(name,data):
    if Path(name).suffix.lower()=='.key' or b'PRIVATE KEY-----' in data:
        try:
            if b'-----BEGIN' in data:serialization.load_pem_private_key(data,password=None)
            else:serialization.load_der_private_key(data,password=None)
        except (ValueError,TypeError):raise ValueError('Invalid private key; use unencrypted PEM/DER in the private local certificate folder') from None
    else:
        try:load(data)
        except ValueError:
            try:serialization.load_der_private_key(data,password=None)
            except (ValueError,TypeError):raise ValueError('Invalid X.509 certificate or private key') from None


def install(app,folder):
    app['certificate_folder']=Path(folder)/'certificates'
    app.add_routes([web.get('/api/security/certificates',inventory)])
