/*
    SPDX-License-Identifier: LGPL-2.1-only OR LGPL-3.0-only OR LicenseRef-KDE-Accepted-LGPL
    SPDX-FileCopyrightText: 2025 GlobalProtect VPN Plugin
*/

#include "gpclientauthwidget.h"

#include <NetworkManagerQt/Utils>
#include <KLocalizedString>
#include <QFormLayout>
#include <QLabel>
#include <QLineEdit>

namespace
{
QString labelForSecret(const QString &key)
{
    if (key == QLatin1String("username")) {
        return i18n("Username");
    }
    if (key == QLatin1String("password")) {
        return i18n("Password");
    }
    if (key == QLatin1String("otp")) {
        return i18n("Token / one-time code");
    }

    QString label = key;
    label.replace(QLatin1Char('-'), QLatin1Char(' '));
    if (!label.isEmpty()) {
        label[0] = label.at(0).toUpper();
    }
    return label;
}
}

GpclientAuthWidget::GpclientAuthWidget(const NetworkManager::VpnSetting::Ptr &setting,
                                       const QStringList &hints,
                                       QWidget *parent)
    : SettingWidget(setting, hints, parent)
{
    auto *layout = new QFormLayout(this);
    layout->setFieldGrowthPolicy(QFormLayout::ExpandingFieldsGrow);

    const QString messageHintPrefix = QStringLiteral("x-vpn-message:");
    QString message;
    for (const QString &hint : hints) {
        if (hint.startsWith(messageHintPrefix)) {
            message = hint.mid(messageHintPrefix.size());
        } else if (!hint.isEmpty() && !m_secretKeys.contains(hint)) {
            m_secretKeys.append(hint);
        }
    }

    // NetworkManager requests the initial password with an empty hints list.
    // Interactive username, password, and OTP prompts include their secret key.
    if (m_secretKeys.isEmpty() && setting
        && setting->data().value(QLatin1String("auth-mode")) == QLatin1String("credentials")
        && setting->data().value(QLatin1String("password-flags")) != QLatin1String("4")) {
        m_secretKeys.append(QLatin1String("password"));
    }

    if (!message.isEmpty()) {
        auto *messageLabel = new QLabel(message, this);
        messageLabel->setTextFormat(Qt::PlainText);
        messageLabel->setWordWrap(true);
        layout->addRow(messageLabel);
    }

    for (const QString &key : m_secretKeys) {
        auto *field = new QLineEdit(this);
        if (key != QLatin1String("username") && key != QLatin1String("otp")) {
            field->setEchoMode(QLineEdit::Password);
        }

        if (m_fields.isEmpty()) {
            setFocusProxy(field);
        }
        m_fields.insert(key, field);

        auto *label = new QLabel(labelForSecret(key), this);
        label->setTextFormat(Qt::PlainText);
        label->setBuddy(field);
        layout->addRow(label, field);
    }
}

QVariantMap GpclientAuthWidget::setting() const
{
    NMStringMap secrets;
    for (const QString &key : m_secretKeys) {
        const QString value = m_fields.value(key)->text();
        if (!value.isEmpty()) {
            secrets.insert(key, value);
        }
    }

    QVariantMap result;
    result.insert(QStringLiteral("secrets"), QVariant::fromValue<NMStringMap>(secrets));
    return result;
}
