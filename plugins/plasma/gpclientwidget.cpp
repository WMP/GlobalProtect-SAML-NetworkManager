/*
    SPDX-License-Identifier: LGPL-2.1-only OR LGPL-3.0-only OR LicenseRef-KDE-Accepted-LGPL
    SPDX-FileCopyrightText: 2025 GlobalProtect VPN Plugin
*/

#include "gpclientwidget.h"
#include "ui_gpclientwidget.h"

#include <NetworkManagerQt/Utils>
#include <QCheckBox>
#include <QComboBox>
#include <QFileInfo>
#include <QLineEdit>
#include <QString>
#include <QStringList>

/* First entry of the preferred-gateway combo: let the portal decide. Stored as
 * no vpn.data value at all, so existing profiles keep behaving the same. */
static const QString gatewayAutoLabel = QStringLiteral("First proposed by portal (automatic)");

/* Browser values understood by the service (see resolve_browser()) */
static const QStringList browserValues = {
    QStringLiteral("edge"),
    QStringLiteral("firefox"),
    QStringLiteral("chrome"),
    QStringLiteral("chromium"),
    QStringLiteral("default"),
};

GpclientWidget::GpclientWidget(const NetworkManager::VpnSetting::Ptr &setting, QWidget *parent)
    : SettingWidget(setting, parent)
    , m_ui(new Ui::GpclientWidget)
    , m_setting(setting)
{
    m_ui->setupUi(this);

    m_ui->browserComboBox->addItems(browserValues);
    m_ui->browserComboBox->addItem(QStringLiteral("Custom..."));

    m_ui->authModeComboBox->addItem(QStringLiteral("Browser (SAML, passkey, 2FA)"));
    m_ui->authModeComboBox->addItem(QStringLiteral("Username and password (RSA token)"));

    m_ui->preferredGatewayComboBox->addItem(gatewayAutoLabel);

    m_ui->fixOpensslComboBox->addItem(QStringLiteral("Automatic (enable when the portal needs it)"));
    m_ui->fixOpensslComboBox->addItem(QStringLiteral("Always on"));
    m_ui->fixOpensslComboBox->addItem(QStringLiteral("Never"));

    // Load configuration if available
    loadConfig(setting);

    // Connect signals for change notification
    connect(m_ui->gatewayLineEdit, &QLineEdit::textChanged, this, &GpclientWidget::settingChanged);
    connect(m_ui->asGatewayCheckBox, &QCheckBox::toggled, this, &GpclientWidget::settingChanged);
    connect(m_ui->preferredGatewayComboBox, &QComboBox::currentTextChanged, this, &GpclientWidget::settingChanged);
    connect(m_ui->authModeComboBox, &QComboBox::currentTextChanged, this, &GpclientWidget::settingChanged);
    connect(m_ui->usernameLineEdit, &QLineEdit::textChanged, this, &GpclientWidget::settingChanged);
    connect(m_ui->browserComboBox, &QComboBox::currentTextChanged, this, &GpclientWidget::settingChanged);
    connect(m_ui->dnsLineEdit, &QLineEdit::textChanged, this, &GpclientWidget::settingChanged);
    connect(m_ui->hipCheckBox, &QCheckBox::toggled, this, &GpclientWidget::settingChanged);
    connect(m_ui->fixOpensslComboBox, &QComboBox::currentTextChanged, this, &GpclientWidget::settingChanged);

    auto update_valid = [this]() {
        Q_EMIT validChanged(isValid());
    };
    connect(m_ui->gatewayLineEdit, &QLineEdit::textChanged, this, update_valid);
    connect(m_ui->browserComboBox, &QComboBox::currentTextChanged, this, update_valid);
    connect(m_ui->browserComboBox, &QComboBox::editTextChanged, this, update_valid);
    connect(m_ui->dnsLineEdit, &QLineEdit::textChanged, this, update_valid);

    watchChangedSetting();
    update_valid();
}

GpclientWidget::~GpclientWidget()
{
    delete m_ui;
}

void GpclientWidget::loadConfig(const NetworkManager::Setting::Ptr &setting)
{
    NetworkManager::VpnSetting::Ptr vpnSetting = setting.staticCast<NetworkManager::VpnSetting>();

    if (vpnSetting) {
        const NMStringMap data = vpnSetting->data();
        m_originalData = data;

        // Load the portal / gateway address
        if (data.contains(QLatin1String("gateway"))) {
            m_ui->gatewayLineEdit->setText(data.value(QLatin1String("gateway")));
        }

        // The address is a gateway, not a portal
        m_ui->asGatewayCheckBox->setChecked(
            data.value(QLatin1String("as-gateway")).toLower() == QLatin1String("true"));

        // Offer the gateways discovered during the last successful connection
        const QString cached = data.value(QLatin1String("gateway-list"));
        if (!cached.isEmpty()) {
            const QStringList gateways = cached.split(QLatin1Char(';'), Qt::SkipEmptyParts);
            for (const QString &gateway : gateways) {
                const QString entry = gateway.trimmed();
                if (!entry.isEmpty()) {
                    m_ui->preferredGatewayComboBox->addItem(entry);
                }
            }
        }

        const QString preferred = data.value(QLatin1String("preferred-gateway"));
        if (preferred.isEmpty()) {
            m_ui->preferredGatewayComboBox->setCurrentIndex(0);
        } else {
            const int index = m_ui->preferredGatewayComboBox->findText(preferred);
            if (index >= 0) {
                m_ui->preferredGatewayComboBox->setCurrentIndex(index);
            } else {
                // A gateway that is not in the cache yet - keep it as typed
                m_ui->preferredGatewayComboBox->setEditText(preferred);
            }
        }

        // Authentication mode (SAML is the default)
        m_ui->authModeComboBox->setCurrentIndex(
            data.value(QLatin1String("auth-mode")) == QLatin1String("credentials") ? 1 : 0);

        // Username for portals that ask for credentials on the terminal
        m_ui->usernameLineEdit->setText(data.value(QLatin1String("username")));

        // Load Browser
        const QString browserValue = data.value(QLatin1String("browser"), browserValues.first());
        const int browserIndex = m_ui->browserComboBox->findText(browserValue);
        if (browserIndex >= 0) {
            m_ui->browserComboBox->setCurrentIndex(browserIndex);
        } else {
            // An older profile with a full path - keep it as typed
            m_ui->browserComboBox->setEditText(browserValue);
        }

        // Load DNS
        if (data.contains(QLatin1String("dns"))) {
            m_ui->dnsLineEdit->setText(data.value(QLatin1String("dns")));
        }

        // Legacy TLS renegotiation workaround; the service switches this to
        // "Always on" itself after a connection that needed it
        const QString fixOpenssl = data.value(QLatin1String("fix-openssl")).toLower();
        if (fixOpenssl == QLatin1String("true")) {
            m_ui->fixOpensslComboBox->setCurrentIndex(1);
        } else if (fixOpenssl == QLatin1String("false")) {
            m_ui->fixOpensslComboBox->setCurrentIndex(2);
        } else {
            m_ui->fixOpensslComboBox->setCurrentIndex(0);
        }

        // Load HIP (default: enabled)
        const QString hipValue = data.value(QLatin1String("hip"), QLatin1String("true"));
        m_ui->hipCheckBox->setChecked(hipValue.toLower() == QLatin1String("true"));
    }
}

QVariantMap GpclientWidget::setting() const
{
    NetworkManager::VpnSetting setting;
    setting.setServiceType(QLatin1String("org.freedesktop.NetworkManager.gpclient"));

    // Start from what the profile already had, so keys this widget does not show
    // (gateway-list written by the service, dns-domains) survive a save
    NMStringMap data = m_originalData;

    // Save the portal / gateway address
    if (!m_ui->gatewayLineEdit->text().isEmpty()) {
        data.insert(QLatin1String("gateway"), m_ui->gatewayLineEdit->text());
    } else {
        data.remove(QLatin1String("gateway"));
    }

    // Save "address is a gateway"
    if (m_ui->asGatewayCheckBox->isChecked()) {
        data.insert(QLatin1String("as-gateway"), QLatin1String("true"));
    } else {
        data.remove(QLatin1String("as-gateway"));
    }

    // Save the preferred gateway (the automatic entry stores nothing)
    const QString preferred = m_ui->preferredGatewayComboBox->currentText().trimmed();
    if (!preferred.isEmpty() && preferred != gatewayAutoLabel) {
        data.insert(QLatin1String("preferred-gateway"), preferred);
    } else {
        data.remove(QLatin1String("preferred-gateway"));
    }

    // Save the authentication mode (SAML is the default and stores nothing)
    if (m_ui->authModeComboBox->currentIndex() == 1) {
        data.insert(QLatin1String("auth-mode"), QLatin1String("credentials"));
    } else {
        data.remove(QLatin1String("auth-mode"));
    }

    // Save the username
    if (!m_ui->usernameLineEdit->text().isEmpty()) {
        data.insert(QLatin1String("username"), m_ui->usernameLineEdit->text());
    } else {
        data.remove(QLatin1String("username"));
    }

    // Save Browser
    const QString browserText = m_ui->browserComboBox->currentText().trimmed();
    if (!browserText.isEmpty() && browserText != QLatin1String("Custom...")) {
        data.insert(QLatin1String("browser"), browserText);
    } else {
        data.remove(QLatin1String("browser"));
    }

    // Save DNS
    if (!m_ui->dnsLineEdit->text().isEmpty()) {
        data.insert(QLatin1String("dns"), m_ui->dnsLineEdit->text());
    } else {
        data.remove(QLatin1String("dns"));
    }

    // Save the legacy TLS workaround. Automatic stores nothing - the service
    // reads a missing key as "auto" and switches the profile to "true" once a
    // portal needs it.
    switch (m_ui->fixOpensslComboBox->currentIndex()) {
    case 1:
        data.insert(QLatin1String("fix-openssl"), QLatin1String("true"));
        break;
    case 2:
        data.insert(QLatin1String("fix-openssl"), QLatin1String("false"));
        break;
    default:
        data.remove(QLatin1String("fix-openssl"));
        break;
    }

    // Save HIP
    data.insert(QLatin1String("hip"),
                m_ui->hipCheckBox->isChecked() ? QLatin1String("true") : QLatin1String("false"));

    setting.setData(data);

    return setting.toMap();
}

bool GpclientWidget::isValid() const
{
    // The portal / gateway address is required
    return !m_ui->gatewayLineEdit->text().isEmpty();
}
