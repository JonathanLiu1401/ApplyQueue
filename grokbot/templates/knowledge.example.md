# knowledge.json (example)

Copy into `{{STATE_DIR}}/knowledge.json`. Build it from the resume(s) and questionnaire section I,
then show the user a summary and ask them to correct it. Every line must trace back to the resume
or the user's own words. Agents use it for essays, "why us", "tell us about a project" and
cover letters, never for facts that belong in `profile.json`.

```json
{
  "positioning": "Computer engineering student who builds small embedded systems end to end: sensors, firmware, and the tooling to test them.",
  "research_interests": ["low-power sensing", "test automation"],
  "anchor_projects": [
    {
      "name": "Example Weather Station",
      "role": "Solo",
      "what": "Battery-powered weather station with a microcontroller, three sensors and a small web dashboard.",
      "my_part": "Firmware, power budget, enclosure, dashboard.",
      "result": "Ran 30 days on one charge; logged 40k readings.",
      "skills": ["C", "I2C", "Python", "power measurement"],
      "use_for": ["embedded", "firmware", "hardware test"]
    },
    {
      "name": "Course Scheduler Tool",
      "role": "Team of 3",
      "what": "Web tool that turns a course list into conflict-free weekly schedules.",
      "my_part": "Backend and the constraint solver.",
      "result": "Used by about 200 students in one term.",
      "skills": ["Python", "SQL"],
      "use_for": ["data", "software"]
    }
  ],
  "experience_timeline": [
    {
      "org": "Example State University Makerspace",
      "title": "Student Technician",
      "dates": "2023-10 to present",
      "bullets": ["Help about 50 students a week with soldering, 3D printing and test equipment."]
    }
  ],
  "education_detail": {
    "relevant_courses": ["Digital Logic", "Data Structures", "Signals and Systems"],
    "honors": []
  },
  "awards": [],
  "leadership": ["Workshop lead, student engineering club"],
  "skills": {
    "languages": ["C", "Python", "SQL"],
    "tools": ["oscilloscope", "logic analyzer", "Git", "Linux"],
    "spoken_languages": ["English"]
  },
  "why_company_ingredients": {
    "_rule": "Combine one real fact about the company from its own site or the posting with one real project or interest from this file. Never invent either side.",
    "likes": ["products people hold in their hands", "teams that test their own hardware"],
    "learning_goals": ["production firmware practices", "hardware bring-up"]
  }
}
```
