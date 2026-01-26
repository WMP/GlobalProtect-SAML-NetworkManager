cmake_minimum_required(VERSION 3.16)

project(plasmanetworkmanagement_gpclientui)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Find required packages
find_package(ECM REQUIRED NO_MODULE)
set(CMAKE_MODULE_PATH ${ECM_MODULE_PATH} ${CMAKE_MODULE_PATH})

include(KDEInstallDirs)
include(KDECMakeSettings)
include(KDECompilerSettings NO_POLICY_SCOPE)
include(ECMQtDeclareLoggingCategory)
include(FeatureSummary)

find_package(Qt5 REQUIRED COMPONENTS Core Widgets Network DBus)
find_package(KF5 REQUIRED COMPONENTS I18n Service WidgetsAddons)
find_package(KF5NetworkManagerQt REQUIRED)

# Find plasma-nm libraries (installed with plasma-nm package)
find_library(PLASMANM_INTERNAL plasmanm_internal PATHS /usr/lib/x86_64-linux-gnu)
find_library(PLASMANM_EDITOR plasmanm_editor PATHS /usr/lib/x86_64-linux-gnu)

if(NOT PLASMANM_INTERNAL OR NOT PLASMANM_EDITOR)
    message(FATAL_ERROR "plasma-nm libraries not found. Please install plasma-nm package.")
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
    Qt5::Core
    Qt5::Widgets
    KF5::I18n
    KF5::Service
    KF5::WidgetsAddons
    KF5::NetworkManagerQt
    ${PLASMANM_INTERNAL}
    ${PLASMANM_EDITOR}
)

# Install plugin
install(TARGETS plasmanetworkmanagement_gpclientui
        DESTINATION ${KDE_INSTALL_PLUGINDIR}/plasma/network/vpn)

# Install metadata
install(FILES plasmanetworkmanagement_gpclientui.json
        DESTINATION ${KDE_INSTALL_PLUGINDIR}/plasma/network/vpn)

install(FILES plasmanetworkmanagement_gpclientui.desktop
        DESTINATION ${KDE_INSTALL_KSERVICES5DIR})

feature_summary(WHAT ALL INCLUDE_QUIET_PACKAGES FATAL_ON_MISSING_REQUIRED_PACKAGES)
