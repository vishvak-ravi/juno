# Quiz-writing instructions

You are writing a short comprehension quiz about a pull request. The person
taking it is the PR author or a reviewer. A good question can only be answered
by someone who actually read and understood the diff, not by pattern-matching
the PR title.

## Steps

1. Read the diff: `gh pr diff <PR_NUMBER>` and `gh pr view <PR_NUMBER>`.
   Read surrounding files with the Read tool when the diff alone is ambiguous.
2. Read `pr2test/schema.py`. Your output must validate as `Quiz`.
3. Write the quiz as JSON to `.pr2test/quiz.json`. Write that file and nothing
   else. Do not post comments. Do not edit any other file.

## What to ask

- 3 to 5 questions. Fewer for a tiny diff.
- Ask about behavior, edge cases, why a change was made, what breaks if a line
  is removed, what a function now returns for a specific input.
- Every option must be plausible to someone who skimmed. Exactly one is right.
- Put `anchor` on questions tied to a specific file, like `src/foo.py:42`.
- `explanation` is shown after grading. Make it teach something.
- Never ask about things outside the diff, formatting, or trivia like line
  counts.

## Shape

```json
{
  "version": 1,
  "title": "Quiz for PR #12: add retry to fetch_user",
  "questions": [
    {
      "type": "mcq",
      "id": "q1",
      "prompt": "After this change, what does fetch_user return when every retry fails?",
      "options": [
        {"key": "A", "text": "None"},
        {"key": "B", "text": "It raises the last exception"},
        {"key": "C", "text": "An empty dict"},
        {"key": "D", "text": "It blocks until the service recovers"}
      ],
      "answer": "B",
      "explanation": "The loop re-raises on the final attempt; see the `raise` after the for loop.",
      "anchor": "src/users.py:58"
    }
  ]
}
```

Option keys must be A, B, C... in order. Question ids must be q1, q2, q3... in
order.
