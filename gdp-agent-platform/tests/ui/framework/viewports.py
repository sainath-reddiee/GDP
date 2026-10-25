"""Device / viewport matrix and media emulation profiles."""

VIEWPORTS = {
    "mobile-s": {"width": 320, "height": 568},
    "mobile": {"width": 390, "height": 844},
    "tablet-portrait": {"width": 768, "height": 1024},
    "tablet-landscape": {"width": 1024, "height": 768},
    "laptop": {"width": 1366, "height": 768},
    "desktop": {"width": 1440, "height": 900},
    "full-hd": {"width": 1920, "height": 1080},
    "ultrawide": {"width": 2560, "height": 1080},
}

MOBILE = ("mobile-s", "mobile", "tablet-portrait")

ZOOM_LEVELS = [0.5, 0.8, 1.25, 1.5, 2.0]
