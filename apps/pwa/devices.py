"""iOS startup-image device list: (css_width, css_height, pixel_ratio, label).

Add a device by appending one line; `generate_pwa_assets` and `{% pwa_head %}` both read this.
"""

IOS_DEVICES = [
    (440, 956, 3, "iPhone 16 Pro Max"),
    (402, 874, 3, "iPhone 16 Pro"),
    (430, 932, 3, "iPhone 14/15 Pro Max, 15/16 Plus"),
    (393, 852, 3, "iPhone 14/15 Pro, 15, 16"),
    (428, 926, 3, "iPhone 12/13 Pro Max, 14 Plus"),
    (390, 844, 3, "iPhone 12, 13, 14"),
    (414, 896, 3, "iPhone XS Max, 11 Pro Max"),
    (414, 896, 2, "iPhone XR, 11"),
    (375, 812, 3, "iPhone X, XS, 11 Pro, 12/13 mini"),
    (414, 736, 3, "iPhone 8 Plus"),
    (375, 667, 2, "iPhone 8, SE 2nd/3rd gen"),
    (1024, 1366, 2, 'iPad Pro 12.9"'),
    (834, 1194, 2, 'iPad Pro 11"'),
    (820, 1180, 2, "iPad Air"),
    (810, 1080, 2, 'iPad 10.2"'),
    (768, 1024, 2, "iPad mini / older iPads"),
]

ICON_SIZES = [72, 96, 128, 144, 152, 192, 256, 384, 512]
MASKABLE_SIZES = [192, 512]


def splash_images():
    """Yield dicts for every portrait + landscape splash."""
    for w, h, r, label in IOS_DEVICES:
        pw, ph = w * r, h * r
        yield {"w": w, "h": h, "r": r, "orientation": "portrait", "px_w": pw, "px_h": ph, "label": label,
               "file": f"pwa/splash/splash-{pw}x{ph}.png"}
        yield {"w": w, "h": h, "r": r, "orientation": "landscape", "px_w": ph, "px_h": pw, "label": label,
               "file": f"pwa/splash/splash-{ph}x{pw}.png"}
