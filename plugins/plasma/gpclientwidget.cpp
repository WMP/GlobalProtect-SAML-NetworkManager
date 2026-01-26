/*
    SPDX-License-Identifier: LGPL-2.1-only OR LGPL-3.0-only OR LicenseRef-KDE-Accepted-LGPL
    SPDX-FileCopyrightText: 2025 GlobalProtect VPN Plugin
*/

#include "gpclientwidget.h"
#include "ui_gpclientwidget.h"

#include <NetworkManagerQt/Utils>
#include <QComboBox>
#include <QFileInfo>
#include <QLineEdit>
#include <QString>

GpclientWidget::GpclientWidget(const NetworkManager::VpnSetting::Ptr &setting, QWidget *parent)
    : SettingWidget(setting, parent)
    , m_ui(new Ui::GpclientWidget)
    , m_setting(setting)
{
    m_ui->setupUi(this);

    // Populate browser combo box with existing browsers (compatibility)
    const QStringList browsers = {
        QStringLiteral("/usr/libexec/gpclient/edge-wrapper"),
        QStringLiteral("/usr/bin/firefox"),
        QStringLiteral("/usr/bin/chromium"),
        QStringLiteral("/usr/bin/google-chrome")
    };
    
    for (const QString &browser : browsers) {
        if (QFileInfo::exists(browser)) {
            m_ui->browserComboBox->addItem(browser);
        }
    }
    
    // Always add "Custom..." option at the end
    m_ui->browserComboBox->addItem(QStringLiteral("Custom..."));

    // Load configuration if available
    loadConfig(setting);

    // Connect signals for change notification
    connect(m_ui->gatewayLineEdit, &QLineEdit::textChanged, this, &GpclientWidget::settingChanged);
    connect(m_ui->browserComboBox, &QComboBox::currentTextChanged, this, &GpclientWidget::settingChanged);
    connect(m_ui->dnsLineEdit, &QLineEdit::textChanged, this, &GpclientWidget::settingChanged);

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

        // Load Gateway
        if (data.contains(QLatin1String("gateway"))) {
            m_ui->gatewayLineEdit->setText(data.value(QLatin1String("gateway")));
        }

        // Load Browser
        QString browserValue = data.value(QLatin1String("browser"), QLatin1String("/usr/libexec/gpclient/edge-wrapper"));
        int index = m_ui->browserComboBox->findText(browserValue);
        if (index >= 0) {
            m_ui->browserComboBox->setCurrentIndex(index);
        } else {
            // Custom value not in predefined list
            m_ui->browserComboBox->setEditText(browserValue);
        }

        // Load DNS
        if (data.contains(QLatin1String("dns"))) {
            m_ui->dnsLineEdit->setText(data.value(QLatin1String("dns")));
        }
    }
}

QVariantMap GpclientWidget::setting() const
{
    NetworkManager::VpnSetting setting;
    setting.setServiceType(QLatin1String("org.freedesktop.NetworkManager.gpclient"));

    NMStringMap data;

    // Save Gateway
    if (!m_ui->gatewayLineEdit->text().isEmpty()) {
        data.insert(QLatin1String("gateway"), m_ui->gatewayLineEdit->text());
    }

    // Save Browser
    QString browserText = m_ui->browserComboBox->currentText();
    if (!browserText.isEmpty() && browserText != QLatin1String("Custom...")) {
        data.insert(QLatin1String("browser"), browserText);
    }

    // Save DNS
    if (!m_ui->dnsLineEdit->text().isEmpty()) {
        data.insert(QLatin1String("dns"), m_ui->dnsLineEdit->text());
    }

    setting.setData(data);

    return setting.toMap();
}

bool GpclientWidget::isValid() const
{
    // Gateway is required
    return !m_ui->gatewayLineEdit->text().isEmpty();
}
