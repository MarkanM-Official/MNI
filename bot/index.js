/**
 * MNI Automation Manager - Node.js Bot Layer
 * Starts Telegram + Discord bots simultaneously
 * Supports hot-reload when tokens are updated in admin panel
 */
require('dotenv').config({ path: require('path').join(__dirname, '../.env') });

const { initTelegramBot } = require('./telegram/bot');
const { initDiscordBot   } = require('./discord/bot');

console.log('MNI Automation Manager bot layer starting...');

// Start both bots (async initialization)
(async () => {
  await initTelegramBot();
})().catch(err => console.error('❌ Error initializing Telegram bot:', err));

initDiscordBot();

// Graceful shutdown
process.on('SIGINT', () => {
  console.log('\nMNI Automation Manager shutting down...');
  process.exit(0);
});
