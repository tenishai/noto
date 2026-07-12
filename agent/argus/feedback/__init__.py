"""The gojira rightsizing feedback loop: measure in Mimir, distill, POST to gojira.

Closes the deploy-observe-resize loop: gojira deploys, Argus observes, Argus feeds
distilled usage evidence back, gojira opens the reviewed rightsizing PR.
"""
