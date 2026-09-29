/**
 * Wellness & Hydration Reminder Engine
 * Periodically reminds user to rest, hydrate, and stretch.
 */
export class WellnessEngine {
  constructor(speechBubble, options = {}) {
    this.speechBubble = speechBubble;
    this.enabled = true;
    this.intervalMinutes = options.intervalMinutes || 12; // default reminder every 12 mins
    this.timer = null;

    this.reminders = [
      { text: "💧 Hydration check! Take a sip of cold water.", type: "wellness" },
      { text: "👀 Eye rest! Look at an object 20 feet away for 20 seconds.", type: "wellness" },
      { text: "🧘 Posture check! Straighten your spine and relax your shoulders.", type: "wellness" },
      { text: "🙆‍♂️ Quick stretch! Roll your neck and stretch your wrists.", type: "wellness" },
      { text: "☕ You've been coding hard! Take a deep breath and smile.", type: "wellness" },
      { text: "🚶 Stand up and walk around for 60 seconds to boost focus!", type: "wellness" },
      { text: "💧 Don't forget your water bottle! Brains work best hydrated.", type: "wellness" },
      { text: "✨ Micro-break! Unclench your jaw and rest your hands.", type: "wellness" }
    ];

    this.currentIndex = 0;
    this.startTimer();
  }

  startTimer() {
    this.stopTimer();
    this.timer = setInterval(() => {
      if (this.enabled) {
        this.triggerRandomReminder();
      }
    }, this.intervalMinutes * 60 * 1000);
  }

  stopTimer() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  triggerRandomReminder() {
    const reminder = this.reminders[this.currentIndex % this.reminders.length];
    this.currentIndex++;
    this.speechBubble.show(reminder.text, {
      duration: 5500,
      chime: true,
      speak: false,
      type: "wellness"
    });
  }

  setEnabled(enabled) {
    this.enabled = enabled;
    if (enabled && !this.timer) {
      this.startTimer();
    } else if (!enabled) {
      this.stopTimer();
    }
  }
}
