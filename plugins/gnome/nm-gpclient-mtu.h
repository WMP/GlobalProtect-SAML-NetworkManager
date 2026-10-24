/* Tunnel MTU (vpn.data "mtu"), shared by the GTK3, GTK4 and Plasma editors.
 *
 * Plain C with no GLib, NetworkManager or Qt dependency: it compiles as C and
 * as C++. The same rule is in the service (parse_mtu in
 * service/nm-gpclient-service.py); tests/unit/test_mtu_header.py checks that
 * both give the same result.
 *
 * 0 = automatic: no key is stored. gpclient --mtu reaches openconnect as the
 * requested MTU, which it uses ONLY when the gateway sends no MTU of its own.
 * openconnect raises values below 576 to 576, so the editors do the same. */

#ifndef NM_GPCLIENT_MTU_H
#define NM_GPCLIENT_MTU_H

#define MTU_MIN 576
#define MTU_MAX 1500

/* The only whitespace that is stripped: space, \t, \n, \v, \f, \r */
static inline int
nm_gpclient_mtu_is_space (char c)
{
    return c == ' ' || (c >= '\t' && c <= '\r');
}

/* Parse the stored mtu text. Result:
 *   0                   automatic: NULL, empty or only whitespace, or only
 *                       zeros (with optional whitespace around them)
 *   MTU_MIN..MTU_MAX    the value; 1..575 is raised to MTU_MIN
 *   -1                  invalid: anything else (sign, dot, inner space,
 *                       non-ASCII, more than MTU_MAX)
 * After the optional whitespace the text must be ASCII digits only; leading
 * zeros are fine. */
static inline int
nm_gpclient_mtu_parse (const char *text)
{
    int mtu = 0;
    int digits = 0;

    if (!text)
        return 0;
    while (nm_gpclient_mtu_is_space (*text))
        text++;
    for (; *text >= '0' && *text <= '9'; text++) {
        digits++;
        mtu = mtu * 10 + (*text - '0');
        if (mtu > MTU_MAX)
            return -1;
    }
    while (nm_gpclient_mtu_is_space (*text))
        text++;
    if (*text != '\0')
        return -1;
    if (digits == 0 || mtu == 0)
        return 0;
    return mtu < MTU_MIN ? MTU_MIN : mtu;
}

#endif /* NM_GPCLIENT_MTU_H */
