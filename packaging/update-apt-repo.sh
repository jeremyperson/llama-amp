#!/bin/bash
# Rebuild and push the Llama Amp apt repository (jeremyperson/llama-amp-apt,
# served at https://jeremyperson.github.io/llama-amp-apt) with the newest
# .deb from packaging/dist/. Run after build-deb.sh as part of a release.
#
# Requires: the "Llama Amp APT Repository" GPG key in the local keyring,
# and a checkout of the repo at $APT_REPO (clone it once if missing).
set -euo pipefail
cd "$(dirname "$0")"

APT_REPO="${APT_REPO:-$HOME/Apps/llama-amp-apt}"
KEY_NAME="Llama Amp APT Repository"

if [ ! -d "$APT_REPO/.git" ]; then
  echo "apt repo checkout not found — cloning..."
  git clone https://github.com/jeremyperson/llama-amp-apt.git "$APT_REPO"
fi

DEB=$(ls -t dist/llama-amp_*_all.deb | head -1)
[ -n "$DEB" ] || { echo "no .deb in dist/ — run build-deb.sh first"; exit 1; }
echo "Publishing $DEB"

FPR=$(gpg --list-keys --with-colons "$KEY_NAME" | awk -F: '/^fpr/{print $10; exit}')
[ -n "$FPR" ] || { echo "GPG key '$KEY_NAME' not found in keyring"; exit 1; }

cd "$APT_REPO"
mkdir -p pool/main/l/llama-amp \
  dists/stable/main/binary-amd64 dists/stable/main/binary-arm64 \
  dists/stable/main/binary-i386
cp "$OLDPWD/$DEB" pool/main/l/llama-amp/

apt-ftparchive packages pool > dists/stable/main/binary-amd64/Packages
for a in arm64 i386; do
  cp dists/stable/main/binary-amd64/Packages "dists/stable/main/binary-$a/Packages"
done
for a in amd64 arm64 i386; do
  gzip -kf9 "dists/stable/main/binary-$a/Packages"
done

apt-ftparchive \
  -o APT::FTPArchive::Release::Origin="Llama Amp" \
  -o APT::FTPArchive::Release::Label="Llama Amp" \
  -o APT::FTPArchive::Release::Suite=stable \
  -o APT::FTPArchive::Release::Codename=stable \
  -o APT::FTPArchive::Release::Architectures="amd64 arm64 i386" \
  -o APT::FTPArchive::Release::Components=main \
  release dists/stable > dists/stable/Release
gpg --default-key "$FPR" -abs --yes -o dists/stable/Release.gpg dists/stable/Release
gpg --default-key "$FPR" --clearsign --yes -o dists/stable/InRelease dists/stable/Release
gpg --export "$FPR" > llama-amp.gpg

VER=$(basename "$DEB" | sed 's/llama-amp_\(.*\)_all.deb/\1/')
git add -A
git commit -m "Publish llama-amp $VER"
git push
echo "apt repo updated: https://jeremyperson.github.io/llama-amp-apt (v$VER)"
