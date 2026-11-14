"""CDOS services: the long-running processes systemd starts (systemd/cdos-*.service).

    platform.py   one object holding what a service needs: the computer (reasoning, corpus, neural core, plugins,
                  components), the robot controller when robotics is on, and paths from config.toml
    api.py        the local API: HTTP over a Unix socket (or TCP on 127.0.0.1 only), JSON in and out
    client.py     a small client for it (the simulated robot uses it; so can LCARS, voice, scripts)

How the services in the design map onto 0.2's processes:

    cdos-core, cdos-neural, cdos-api, cdos-world, cdos-robotics   -> cdos-api.service   (one process: they share the
                                                                     computer, the corpus and the component registry)
    cdos-growth                                                    -> cdos-growth.service + cdos-growth.timer
    the simulated robot                                            -> cdos-robot-sim.service (a client of the API)
    the voice device (Pi)                                          -> computer.service, as before
"""
