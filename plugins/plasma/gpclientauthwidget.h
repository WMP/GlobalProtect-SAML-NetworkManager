/*
    SPDX-License-Identifier: LGPL-2.1-only OR LGPL-3.0-only OR LicenseRef-KDE-Accepted-LGPL
    SPDX-FileCopyrightText: 2025 GlobalProtect VPN Plugin
*/

#ifndef PLASMA_NM_GPCLIENT_AUTH_WIDGET_H
#define PLASMA_NM_GPCLIENT_AUTH_WIDGET_H

#include <NetworkManagerQt/VpnSetting>
#include <QHash>
#include <QStringList>
#include <QVariantMap>

#include <settingwidget.h>

class QLineEdit;

class GpclientAuthWidget final : public SettingWidget
{
public:
    explicit GpclientAuthWidget(const NetworkManager::VpnSetting::Ptr &setting,
                                const QStringList &hints,
                                QWidget *parent = nullptr);

    QVariantMap setting() const override;

private:
    QStringList m_secretKeys;
    QHash<QString, QLineEdit *> m_fields;
};

#endif // PLASMA_NM_GPCLIENT_AUTH_WIDGET_H
