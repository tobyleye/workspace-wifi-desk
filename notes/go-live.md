# Go Live

Right now the WhatsApp assistant works end to end in a test setup: the members are made up and kept
in a Google Sheet, and the WiFi codes come from a mock of the UniFi system. Here's what it would take
to switch it on for real, but first, what it does.

## What it does

Members message a WhatsApp number when they get in. The assistant checks their plan and sends them
their WiFi code straight away, so nobody has to wait for reception.

It's mainly built for members on 3-days-a-week plans, who need a new code every day they come in.
Members on full plans (every day for a week or a month) usually only need one code for the whole
plan, but the assistant serves them too.

It also tells members when their plan ends and answers questions about opening hours and plans.
Anything involving money (payments, renewals, day passes) still goes to reception.

## What I'd need from you

1. **A WhatsApp Business number** for the assistant.
2. **A walkthrough of your WiFi setup (UniFi).** I don't know yet how your UniFi system is set up,
   so the first step is to understand it with you: which controller you run, where it's hosted, and
   whether it can be reached from outside the office. Then we'd create an API key in your UniFi
   settings so the assistant can create the WiFi codes automatically. This is the main setup step,
   and the rest of the plan depends on what we find.
3. **A Google account for the member sheet.**
4. **Your member list and each member's current plan.**

## What changes for reception

- Members get their codes themselves, so reception isn't interrupted every time an existing member
  needs one.
- Reception adds new members and their plans to the system. For now that means adding rows to the
  Google Sheet; later it would be through an internal tool that isn't built yet.
- Reception still handles payments, renewals and anything unusual. The assistant sends those members
  to her.
- Every code the assistant gives out is logged in the sheet, with who got it and when.

## What the assistant will never do on its own

- Give a code to someone without an active plan, or beyond the number of days their plan allows.
- Take payments, renew plans or sell day passes.
- Make up a code. Codes only come from the WiFi system.

## Risks and how they're handled

- **The WiFi system is down.** The assistant apologises and asks the member to try again in a few
  minutes or see reception. Nothing is used up, so they don't lose a day.
- **A code doesn't work.** The assistant can't check this, so it sends the member to reception.
- **A member changes their number.** The assistant won't recognise them, so reception needs to update
  their number in the sheet.
