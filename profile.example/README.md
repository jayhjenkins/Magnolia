# profile.example

The schema + seed for a per-person profile. `profile_lib.bootstrap_profile()`
seeds `profile/` (gitignored) from here: it copies any file that is missing in
`profile/` and never overwrites one that exists, so it is safe to re-run. The
`magnolia` launcher runs it on first launch and onboarding (step 0) runs it again.
Edit your real values in `profile/`, never here.
The engine reads identity and integration facts only via `scripts/profile_lib.py`.
