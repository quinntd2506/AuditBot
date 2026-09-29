#!/usr/bin/env python
import sys
import os
import argparse
import time

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("google-genai is not installed. Please run: pip install google-genai", file=sys.stderr)
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Gemini Drop-in Replacement for Codex CLI")
    parser.add_argument("command", help="Command to run (e.g. exec)")
    parser.add_argument("-C", "--cwd", help="Current working directory")
    parser.add_argument("-s", "--sandbox", help="Sandbox environment name")
    parser.add_argument("--color", help="Color output")
    parser.add_argument("--skip-git-repo-check", action="store_true")
    parser.add_argument("--ephemeral", action="store_true")
    parser.add_argument("--output-last-message", help="Path to write the final output message")
    parser.add_argument("input_file", help="Input file or '-' for stdin")
    parser.add_argument("-m", "--model", help="Model name", default="gemini-2.5-flash")
    parser.add_argument("-c", "--config", help="Model config", action="append")

    args, unknown = parser.parse_known_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable is missing", file=sys.stderr)
        sys.exit(1)

    # Read prompt from stdin
    if args.input_file == "-":
        prompt = sys.stdin.read()
    else:
        with open(args.input_file, "r") as f:
            prompt = f.read()

    client = genai.Client(api_key=api_key)

    try:
        response = client.models.generate_content(
            model=args.model,
            contents=prompt,
        )
        
        output_text = response.text if response.text else ""
        
        # Write to stdout
        print(output_text)
        
        # Write to output file if requested
        if args.output_last_message:
            with open(args.output_last_message, "w") as f:
                f.write(output_text)
                
        sys.exit(0)
        
    except Exception as e:
        print(f"Gemini API Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
