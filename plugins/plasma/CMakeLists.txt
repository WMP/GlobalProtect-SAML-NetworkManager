cmake_minimum_required(VERSION 3.16)

project(plasmanetworkmanagement_gpclientui)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Decide which Qt/KF major version to build against BEFORE we include
# KDEInstallDirs / KDECompilerSettings (those modules assume a Qt version
# the moment they're loaded — once included with the wrong one we cannot
# switch). Ubuntu 22.04/24.04 ship Plasma 5 (Qt5/KF5); Ubuntu 26.04 ships
# Plasma 6 (Qt6/KF6). QT_MAJOR_VERSION can be forced from the command line
# with -DQT_MAJOR_VERSION=6.
find_package(ECM REQUIRED NO_MODULE)
set(CMAKE_MODULE_PATH ${ECM_MODULE_PATH} ${CMAKE_MODULE_PATH})

if(NOT DEFINED QT_MAJOR_VERSION)
    find_package(Qt6 QUIET COMPONENTS Core)
    if(Qt6_FOUND)
        set(QT_MAJOR_VERSION 6)
    else()
        set(QT_MAJOR_VERSION 5)
    endif()
endif()

message(STATUS "Building plasma plugin against Qt${QT_MAJOR_VERSION}/KF${QT_MAJOR_VERSION}")

# ECM honors QT_MAJOR_VERSION for KDE* modules loaded after this point.
include(KDEInstallDirs)
include(KDECMakeSettings)
include(KDECompilerSettings NO_POLICY_SCOPE)
include(ECMQtDeclareLoggingCategory)
include(FeatureSummary)

if(QT_MAJOR_VERSION EQUAL 6)
    find_package(Qt6 REQUIRED COMPONENTS Core Widgets Network DBus)
    find_package(KF6 REQUIRED COMPONENTS I18n Service WidgetsAddons CoreAddons)
    find_package(KF6NetworkManagerQt REQUIRED)
    set(QT_TARGETS Qt6::Core Qt6::Widgets Qt6::Network Qt6::DBus)
    set(KF_TARGETS KF6::I18n KF6::Service KF6::WidgetsAddons KF6::CoreAddons KF6::NetworkManagerQt)
else()
    find_package(Qt5 REQUIRED COMPONENTS Core Widgets Network DBus)
    find_package(KF5 REQUIRED COMPONENTS I18n Service WidgetsAddons)
    find_package(KF5NetworkManagerQt REQUIRED)
    set(QT_TARGETS Qt5::Core Qt5::Widgets)
    set(KF_TARGETS KF5::I18n KF5::Service KF5::WidgetsAddons KF5::NetworkManagerQt)
endif()

# Find plasma-nm libraries (installed with plasma-nm package)
find_library(PLASMANM_INTERNAL plasmanm_internal PATHS /usr/lib/x86_64-linux-gnu)
find_library(PLASMANM_EDITOR plasmanm_editor PATHS /usr/lib/x86_64-linux-gnu)

if(NOT PLASMANM_INTERNAL OR NOT PLASMANM_EDITOR)
    message(FATAL_ERROR "plasma-nm libraries not found. Please install the plasma-nm package.")
endif()

# Plugin sources
set(gpclient_SRCS
    gpclientui.cpp
    gpclientwidget.cpp
)

# UI files
ki18n_wrap_ui(gpclient_SRCS gpclientwidget.ui)

# Build plugin
add_library(plasmanetworkmanagement_gpclientui MODULE ${gpclient_SRCS})

# Add local include directory for plasma-nm headers
target_include_directories(plasmanetworkmanagement_gpclientui PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)

target_link_libraries(plasmanetworkmanagement_gpclientui
    ${QT_TARGETS}
    ${KF_TARGETS}
    ${PLASMANM_INTERNAL}
    ${PLASMANM_EDITOR}
)

# Install plugin
install(TARGETS plasmanetworkmanagement_gpclientui
        DESTINATION ${KDE_INSTALL_PLUGINDIR}/plasma/network/vpn)

# Install metadata
install(FILES plasmanetworkmanagement_gpclientui.json
        DESTINATION ${KDE_INSTALL_PLUGINDIR}/plasma/network/vpn)

# KF5 used .desktop service files (kservices5) for VPN plugin metadata.
# KF6 deprecated that mechanism — the embedded JSON KPluginMetaData is
# everything plasma-nm 6 reads. Mirror what plasma-nm 6 itself does and
# install no .desktop file on KF6.
if(QT_MAJOR_VERSION EQUAL 5)
    install(FILES plasmanetworkmanagement_gpclientui.desktop
            DESTINATION ${KDE_INSTALL_KSERVICES5DIR})
endif()

feature_summary(WHAT ALL INCLUDE_QUIET_PACKAGES FATAL_ON_MISSING_REQUIRED_PACKAGES)
