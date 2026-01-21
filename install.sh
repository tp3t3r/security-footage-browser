#!/bin/bash

set -e

echo "Installing Security Footage Browser..."

# Create user
useradd -r -s /bin/false footage || true

# Create directories
mkdir -p /opt/footage-browser
mkdir -p /var/lib/footage-browser
mkdir -p /etc/footage-browser

# Copy files
cp -r * /opt/footage-browser/ 2>/dev/null || true
cp -r templates /opt/footage-browser/
cp config/app.conf /etc/footage-browser/

# Update config path in scripts
sed -i "s|config/app.conf|/etc/footage-browser/app.conf|g" /opt/footage-browser/parser.py
sed -i "s|config/app.conf|/etc/footage-browser/app.conf|g" /opt/footage-browser/server.py

# Set permissions
chown -R footage:footage /opt/footage-browser
chown -R footage:footage /var/lib/footage-browser
chown -R footage:footage /etc/footage-browser

# Install systemd services
cp footage-parser.service /etc/systemd/system/
cp footage-web.service /etc/systemd/system/
cp footage-browser.target /etc/systemd/system/

# Reload systemd
systemctl daemon-reload

echo "Installation complete!"
echo "Edit /etc/footage-browser/app.conf and then run:"
echo "  systemctl enable footage-parser footage-web"
echo "  systemctl start footage-parser footage-web"
