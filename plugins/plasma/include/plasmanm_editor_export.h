/* Export header for plasmanm_editor library */

#ifndef PLASMANM_EDITOR_EXPORT_H
#define PLASMANM_EDITOR_EXPORT_H

#ifdef plasmanm_editor_EXPORTS
#  define PLASMANM_EDITOR_EXPORT __attribute__((visibility("default")))
#else
#  define PLASMANM_EDITOR_EXPORT __attribute__((visibility("default")))
#endif

#ifndef PLASMANM_EDITOR_NO_EXPORT
#  define PLASMANM_EDITOR_NO_EXPORT __attribute__((visibility("hidden")))
#endif

#ifndef PLASMANM_EDITOR_DEPRECATED
#  define PLASMANM_EDITOR_DEPRECATED __attribute__ ((__deprecated__))
#endif

#ifndef PLASMANM_EDITOR_DEPRECATED_EXPORT
#  define PLASMANM_EDITOR_DEPRECATED_EXPORT PLASMANM_EDITOR_EXPORT PLASMANM_EDITOR_DEPRECATED
#endif

#ifndef PLASMANM_EDITOR_DEPRECATED_NO_EXPORT
#  define PLASMANM_EDITOR_DEPRECATED_NO_EXPORT PLASMANM_EDITOR_NO_EXPORT PLASMANM_EDITOR_DEPRECATED
#endif

#endif /* PLASMANM_EDITOR_EXPORT_H */
