/*
    SPDX-License-Identifier: LGPL-2.1-only OR LGPL-3.0-only OR LicenseRef-KDE-Accepted-LGPL
    SPDX-FileCopyrightText: 2025 GlobalProtect VPN Plugin
*/

// Runs the real GpclientWidget: the connect timeout (vpn.timeout) and the way
// it follows the authentication mode (issue #46).

#include "gpclientwidget.h"

#include <NetworkManagerQt/VpnSetting>
#include <QComboBox>
#include <QLineEdit>
#include <QSignalSpy>
#include <QDoubleSpinBox>
#include <QtTest>

namespace
{
const QString serviceType = QStringLiteral("org.freedesktop.NetworkManager.gpclient");
}

class GpclientWidgetTest : public QObject
{
    Q_OBJECT

private:
    static NetworkManager::VpnSetting::Ptr makeSetting(const NMStringMap &data, uint timeout)
    {
        NetworkManager::VpnSetting::Ptr setting(new NetworkManager::VpnSetting);
        setting->setServiceType(serviceType);
        setting->setData(data);
        setting->setTimeout(timeout);
        return setting;
    }

private Q_SLOTS:
    void timeout_data();
    void timeout();
    void keepsUnknownDataKeys();
    void removesClearedKeys_data();
    void removesClearedKeys();
    void keepsPersistentAndUserName();
    void doesNotInventPersistentAndUserName();
};

void GpclientWidgetTest::timeout_data()
{
    QTest::addColumn<NMStringMap>("data");
    QTest::addColumn<uint>("stored");
    // Steps: "auth:<index>", "spin:<value>", "type:<digits>"
    QTest::addColumn<QStringList>("steps");
    QTest::addColumn<uint>("expectedValue");
    QTest::addColumn<uint>("expectedTimeout");

    const NMStringMap none;
    const NMStringMap gateway{{QStringLiteral("gateway"), QStringLiteral("vpn.example.com")}};
    const NMStringMap credentials{{QStringLiteral("gateway"), QStringLiteral("vpn.example.com")},
                                  {QStringLiteral("auth-mode"), QStringLiteral("credentials")}};

    QTest::newRow("new, browser") << none << 0u << QStringList{} << 360u << 360u;
    QTest::newRow("new, credentials") << none << 0u << QStringList{"auth:1"} << 0u << 0u;
    QTest::newRow("new, credentials, browser again") << none << 0u << QStringList{"auth:1", "auth:0"} << 360u << 360u;
    QTest::newRow("new, spin, credentials") << none << 0u << QStringList{"spin:300", "auth:1"} << 300u << 300u;
    QTest::newRow("new, typed, credentials") << none << 0u << QStringList{"type:500", "auth:1"} << 500u << 500u;
    QTest::newRow("new, stored 90, credentials") << none << 90u << QStringList{"auth:1"} << 90u << 90u;
    QTest::newRow("existing 0 stays 0") << gateway << 0u << QStringList{} << 0u << 0u;
    QTest::newRow("existing credentials 0, browser") << credentials << 0u << QStringList{"auth:0"} << 360u << 360u;
    QTest::newRow("existing credentials 240, browser") << credentials << 240u << QStringList{"auth:0"} << 240u << 240u;
    QTest::newRow("existing credentials 240 kept") << credentials << 240u << QStringList{} << 240u << 240u;
    QTest::newRow("existing credentials 240, typed") << credentials << 240u << QStringList{"type:300"} << 300u << 300u;
    // Above INT_MAX: shown and saved unchanged (guint32)
    QTest::newRow("existing 3000000000") << gateway << 3000000000u << QStringList{} << 3000000000u << 3000000000u;
    QTest::newRow("existing 4294967295") << gateway << 4294967295u << QStringList{} << 4294967295u << 4294967295u;
}

void GpclientWidgetTest::timeout()
{
    QFETCH(NMStringMap, data);
    QFETCH(uint, stored);
    QFETCH(QStringList, steps);
    QFETCH(uint, expectedValue);
    QFETCH(uint, expectedTimeout);

    GpclientWidget widget(makeSetting(data, stored));
    auto *spin = widget.findChild<QDoubleSpinBox *>(QStringLiteral("timeoutSpinBox"));
    auto *authMode = widget.findChild<QComboBox *>(QStringLiteral("authModeComboBox"));
    QVERIFY(spin);
    QVERIFY(authMode);
    auto *edit = spin->findChild<QLineEdit *>();
    QVERIFY(edit);

    QSignalSpy changed(&widget, &SettingWidget::settingChanged);
    for (const QString &step : steps) {
        // Every step must emit settingChanged itself (plasma-nm enables OK on it)
        const int before = changed.count();
        const QString kind = step.section(QLatin1Char(':'), 0, 0);
        const QString arg = step.section(QLatin1Char(':'), 1);
        if (kind == QLatin1String("auth")) {
            authMode->setCurrentIndex(arg.toInt());
        } else if (kind == QLatin1String("spin")) {
            spin->setValue(arg.toDouble());
        } else if (kind == QLatin1String("type")) {
            spin->setFocus();
            edit->selectAll();
            QTest::keyClicks(edit, arg);
        } else {
            QFAIL("unknown step");
        }
        // At least one signal. plasma-nm 5 watches only QSpinBox, so our own emit is
        // needed there. plasma-nm 6 also watches QDoubleSpinBox, so there
        // settingChanged comes twice, which is harmless.
        QVERIFY2(changed.count() > before, qPrintable(QStringLiteral("no settingChanged after ") + step));
    }

    QCOMPARE(static_cast<uint>(spin->value()), expectedValue);
    // No group separators or decimals in the text
    if (expectedValue > 1000u) {
        QCOMPARE(edit->text(), QString::number(expectedValue) + QStringLiteral(" s"));
    }
    const QVariantMap map = widget.setting();
    QVERIFY(map.contains(QStringLiteral("timeout")));
    QCOMPARE(map.value(QStringLiteral("timeout")).toUInt(), expectedTimeout);
}

void GpclientWidgetTest::keepsUnknownDataKeys()
{
    NMStringMap data{{QStringLiteral("gateway"), QStringLiteral("vpn.example.com")},
                     {QStringLiteral("gateway-list"), QStringLiteral("gw1;gw2")}};
    GpclientWidget widget(makeSetting(data, 0));

    NetworkManager::VpnSetting saved;
    saved.fromMap(widget.setting());
    QCOMPARE(saved.data().value(QStringLiteral("gateway-list")), QStringLiteral("gw1;gw2"));
    QCOMPARE(saved.data().value(QStringLiteral("gateway")), QStringLiteral("vpn.example.com"));
    QCOMPARE(saved.serviceType(), serviceType);
}

void GpclientWidgetTest::removesClearedKeys_data()
{
    QTest::addColumn<QString>("key");
    QTest::addColumn<QString>("editName");

    QTest::newRow("username") << "username" << "usernameLineEdit";
    QTest::newRow("dns") << "dns" << "dnsLineEdit";
}

void GpclientWidgetTest::removesClearedKeys()
{
    QFETCH(QString, key);
    QFETCH(QString, editName);

    NMStringMap data{{QStringLiteral("gateway"), QStringLiteral("vpn.example.com")}, {key, QStringLiteral("bob")}};
    GpclientWidget widget(makeSetting(data, 0));
    auto *edit = widget.findChild<QLineEdit *>(editName);
    QVERIFY(edit);
    QCOMPARE(edit->text(), QStringLiteral("bob"));

    // Positive: untouched, the key is saved
    NetworkManager::VpnSetting kept;
    kept.fromMap(widget.setting());
    QCOMPARE(kept.data().value(key), QStringLiteral("bob"));

    // Negative: cleared by the user, the key is gone
    edit->clear();
    NetworkManager::VpnSetting saved;
    saved.fromMap(widget.setting());
    QVERIFY(!saved.data().contains(key));
    QCOMPARE(saved.data().value(QStringLiteral("gateway")), QStringLiteral("vpn.example.com"));
}

void GpclientWidgetTest::keepsPersistentAndUserName()
{
    auto setting = makeSetting({{QStringLiteral("gateway"), QStringLiteral("vpn.example.com")}}, 0);
    setting->setPersistent(true);
    setting->setUsername(QStringLiteral("alice"));
    GpclientWidget widget(setting);

    NetworkManager::VpnSetting saved;
    saved.fromMap(widget.setting());
    QVERIFY(saved.persistent());
    QCOMPARE(saved.username(), QStringLiteral("alice"));
}

void GpclientWidgetTest::doesNotInventPersistentAndUserName()
{
    NetworkManager::VpnSetting::Ptr setting(new NetworkManager::VpnSetting);
    setting->setServiceType(serviceType);
    GpclientWidget widget(setting);

    NetworkManager::VpnSetting saved;
    saved.fromMap(widget.setting());
    QVERIFY(!saved.persistent());
    QVERIFY(saved.username().isEmpty());
}

QTEST_MAIN(GpclientWidgetTest)
#include "gpclientwidgettest.moc"
