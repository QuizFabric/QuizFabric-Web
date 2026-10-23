"""Issue a Let's Encrypt certificate for play.lucasgeitgey.com via HTTP-01.

Uses the `acme`/`josepy` libraries directly (the same ones certbot is built
on) so NO administrator rights are needed - everything is written to
user-writable paths and the certbot_webroot challenge directory.

Usage:
    python get_cert.py            # production certificate
    python get_cert.py --staging  # test run against the staging CA

The quiz server must be running (it serves the ACME challenge route), and
play.lucasgeitgey.com must reach it on external port 80 (router -> 5710).
"""
import argparse
import json
import os
import sys
import time

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

import josepy as jose
from acme import client, messages, crypto_util, challenges
from acme import errors as acme_errors

DOMAIN = "play.lucasgeitgey.com"
WEBROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certbot_webroot")
CERT_ROOT = os.path.join(os.path.expanduser("~"), "quizfabric-certs")
LIVE_DIR = os.path.join(CERT_ROOT, "live", DOMAIN)

PRODUCTION_DIRECTORY = "https://acme-v02.api.letsencrypt.org/directory"
STAGING_DIRECTORY = "https://acme-staging-v02.api.letsencrypt.org/directory"


def load_or_create_account_key(path):
    """Load the saved account key JSON, or create and save a new key."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return jose.JWKRSA.json_loads(f.read())
    key = jose.JWKRSA(key=rsa.generate_private_key(public_exponent=65537, key_size=2048))
    with open(path, "w", encoding="utf-8") as f:
        f.write(key.json_dumps())
    return key


def generate_cert_key():
    """Generate a fresh RSA key for the certificate and return its PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging", action="store_true", help="use the staging CA")
    args = parser.parse_args()

    directory_url = STAGING_DIRECTORY if args.staging else PRODUCTION_DIRECTORY
    account_path = os.path.join(CERT_ROOT, "account-staging.json" if args.staging else "account.json")
    os.makedirs(CERT_ROOT, exist_ok=True)
    os.makedirs(LIVE_DIR, exist_ok=True)

    print(f"[*] Directory: {directory_url}")
    account_key = load_or_create_account_key(account_path)
    net = client.ClientNetwork(account_key, user_agent="QuizFabric/1.0")
    directory = messages.Directory.from_json(net.get(directory_url).json())
    acme_client = client.ClientV2(directory, net)

    print("[*] Registering account (or resuming)...")
    try:
        regr = acme_client.new_account(
            messages.NewRegistration.from_data(
                email=None, terms_of_service_agreed=True))
        acme_client.net.account = regr
    except acme_errors.ConflictError as e:
        # Account already exists: the error carries the account URL in its
        # .location attribute, which the client needs for signing later
        # requests (the JWS 'kid' header).
        regr = messages.RegistrationResource(body=messages.Registration(), uri=e.location)
        acme_client.net.account = regr

    print("[*] Generating certificate key and CSR...")
    cert_key_pem = generate_cert_key()
    csr_pem = crypto_util.make_csr(cert_key_pem, [DOMAIN])

    print("[*] Placing order for", DOMAIN)
    orderr = acme_client.new_order(csr_pem)

    for authzr in orderr.authorizations:
        challb = None
        for c in authzr.body.challenges:
            if isinstance(c.chall, challenges.HTTP01):
                challb = c
                break
        if challb is None:
            print("[!] No HTTP-01 challenge offered - cannot continue")
            return 1
        response, validation = challb.response_and_validation(account_key)
        token = challb.chall.encode("token")
        challenge_file = os.path.join(WEBROOT, ".well-known", "acme-challenge", token)
        os.makedirs(os.path.dirname(challenge_file), exist_ok=True)
        with open(challenge_file, "w", encoding="utf-8") as f:
            f.write(validation)
        print(f"[*] Challenge file written: .well-known/acme-challenge/{token}")
        acme_client.answer_challenge(challb, response)

    print("[*] Waiting for Let's Encrypt to validate (up to 120s)...")
    try:
        finalized = acme_client.poll_and_finalize(orderr)
    except acme_errors.TimeoutError:
        print("[!] Validation timed out - is the server running with the ACME route")
        print("[!] and is external port 80 forwarded to port 5710?")
        return 1
    except acme_errors.ValidationError as e:
        print(f"[!] Validation failed: {e}")
        return 1

    fullchain_path = os.path.join(LIVE_DIR, "fullchain.pem")
    privkey_path = os.path.join(LIVE_DIR, "privkey.pem")
    with open(fullchain_path, "w", encoding="utf-8") as f:
        f.write(finalized.fullchain_pem)
    with open(privkey_path, "wb") as f:
        f.write(cert_key_pem)

    print("[OK] Certificate issued!")
    print(f"    fullchain: {fullchain_path}")
    print(f"    privkey:   {privkey_path}")
    print("[*] Restart the quiz server to start serving HTTPS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
