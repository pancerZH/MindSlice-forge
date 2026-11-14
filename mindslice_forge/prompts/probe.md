# Harness check

You are an agent started by `mindslice-forge`. A long build is about to start, in which sessions
like this one read source documents and write files without anyone watching. This short session
checks that such a session can do what the build needs. Do exactly the following, and nothing else.

1. Run the command `mindslice --version` and keep what it prints.
2. Find the size in bytes of this file, without changing it: `{{source}}`
3. Write the file `{{probe_file}}` with exactly these three lines:
   - the code `{{token}}`
   - what step 1 printed
   - the number from step 2

Do not ask for confirmation. Do not create or change any other file. If step 1 or 2 fails, write
the file all the same, with the error message on that step's line.
