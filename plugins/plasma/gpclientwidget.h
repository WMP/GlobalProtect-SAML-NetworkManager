/*
    SPDX-License-Identifier: LGPL-2.1-only OR LGPL-3.0-only OR LicenseRef-KDE-Accepted-LGPL
    SPDX-FileCopyrightText: 2025 GlobalProtect VPN Plugin
*/

#ifndef PLASMA_NM_GPCLIENT_WIDGET_H
#define PLASMA_NM_GPCLIENT_WIDGET_H

#include <NetworkManagerQt/VpnSetting>
#include <settingwidget.h>

namespace Ui
{
class GpclientWidget;
}

class GpclientWidget : public SettingWidget
{
    Q_OBJECT
public:
    explicit GpclientWidget(const NetworkManager::VpnSetting::Ptr &setting, QWidget *parent = nullptr);
    ~GpclientWidget() override;

    void loadConfig(const NetworkManager::Setting::Ptr &setting) override;
    QVariantMap setting() const override;
    bool isValid() const override;

private:
    Ui::GpclientWidget *m_ui;
    NetworkManager::VpnSetting::Ptr m_setting;
};

#endif // PLASMA_NM_GPCLIENT_WIDGET_H
