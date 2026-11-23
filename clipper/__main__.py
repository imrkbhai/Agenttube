import sys

if len(sys.argv) > 1 and sys.argv[1] == "doctor":
	from clipper.doctor import main
else:
	from clipper.cli import main

raise SystemExit(main())
