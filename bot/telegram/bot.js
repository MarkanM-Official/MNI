/**
 * MNI Automation Manager - Telegram Bot
 * Reads token from admin panel database (via backend API)
 * Falls back to env variable if backend unavailable
 */
const TelegramBot = require('node-telegram-bot-api');
const axios       = require('axios');
const fs          = require('fs');
const os          = require('os');
const path        = require('path');
const { spawn }   = require('child_process');

const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:5000';
const BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || process.env.ADMIN_SECRET_KEY || '';
const PLACEHOLDER_TOKEN = '123456789:ABCdefGHIjklmNOPqrstUVwxyZ';
const warningState = new Map();

let currentBot = null;
let currentToken = null;
let currentConfig = null;
let currentBotUsername = null;
let currentBotId = null;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function telegramCall(fn, retries = 2) {
  try {
    return await fn();
  } catch (err) {
    const retryAfter = err?.response?.body?.parameters?.retry_after;
    if (retryAfter && retries > 0) {
      await sleep((retryAfter + 1) * 1000);
      return telegramCall(fn, retries - 1);
    }
    throw err;
  }
}

function isPlaceholderToken(token) {
  return !token || token.includes('your_') || token === PLACEHOLDER_TOKEN;
}

function maskToken(token) {
  return token ? `${token.substring(0, 8)}...` : 'missing';
}

function parseDataImageUri(uri) {
  const match = /^data:(image\/[a-zA-Z0-9.+-]+);base64,(.+)$/.exec(uri || '');
  if (!match) {
    return null;
  }
  return Buffer.from(match[2], 'base64');
}

function parseDataAudioUri(uri) {
  const match = /^data:audio\/[a-zA-Z0-9.+-]+;base64,(.+)$/.exec(uri || '');
  if (!match) {
    return null;
  }
  return Buffer.from(match[1], 'base64');
}

async function fetchBotConfig() {
  try {
    if (!BOT_SYNC_SECRET) {
      return null;
    }

    const res = await axios.get(`${BACKEND_URL}/api/admin/platform-tokens-bot`, {
      headers: { 'X-Bot-Admin-Token': BOT_SYNC_SECRET },
      timeout: 5000,
    });
    return res.data || null;
  } catch (err) {
    console.log(`⚠️  Telegram: token sync unavailable (${err.message}), falling back to env`);
    return null;
  }
}

async function logModerationEvent(payload) {
  if (!BOT_SYNC_SECRET) return;
  try {
    await axios.post(`${BACKEND_URL}/api/admin/moderation-events-bot`, payload, {
      headers: { 'X-Bot-Admin-Token': BOT_SYNC_SECRET },
      timeout: 5000,
    });
  } catch (err) {
    console.log(`⚠️  Telegram moderation log failed (${err.message})`);
  }
}

function getTrustedMemberIds() {
  const raw = currentConfig?.trusted_member_ids || '';
  return new Set(
    raw.split(',')
      .map((v) => v.trim())
      .filter(Boolean)
  );
}

function moderationEnabled() {
  return String(currentConfig?.moderation_enabled || 'true').toLowerCase() === 'true';
}

function n8nKeyword() {
  return String(currentConfig?.n8n_trigger_keyword || '/workflow').trim() || '/workflow';
}

async function handleN8nWorkflow(bot, msg, text) {
  const webhookUrl = String(currentConfig?.n8n_webhook_url || '').trim();
  const keyword = n8nKeyword();
  const clean = stripBotMention(text || '');
  if (!webhookUrl || !clean.toLowerCase().startsWith(keyword.toLowerCase())) {
    return false;
  }
  const payload = {
    source: 'telegram',
    trigger: keyword,
    message: clean.slice(keyword.length).trim(),
    raw_text: text,
    chat_id: String(msg.chat?.id || ''),
    chat_type: msg.chat?.type || '',
    user_id: String(msg.from?.id || ''),
    username: msg.from?.username || msg.from?.first_name || 'User',
  };
  const res = await axios.post(webhookUrl, payload, { timeout: 30000 });
  const data = res.data || {};
  const reply = data.reply || data.message || data.response || (typeof data === 'string' ? data : 'Workflow triggered.');
  await telegramCall(() => bot.sendMessage(msg.chat.id, String(reply).slice(0, 3900)));
  return true;
}

function shouldReplyInChat(msg, text) {
  const chatType = msg.chat?.type;
  if (chatType === 'private') return true;

  const lowered = (text || '').toLowerCase();
  const username = (currentBotUsername || '').toLowerCase();
  const mention = username ? lowered.includes(`@${username}`) : false;
  const repliedToBot = !!msg.reply_to_message?.from?.is_bot;
  const explicitCommand = /^\/(start|help|invite|form|book|cancel)\b/i.test(text || '');

  return mention || repliedToBot || explicitCommand;
}

function stripBotMention(text) {
  if (!currentBotUsername) return text || '';
  const pattern = new RegExp(`@${currentBotUsername}\\b`, 'ig');
  return String(text || '').replace(pattern, '').replace(/\s+/g, ' ').trim();
}

function containsAbuse(text) {
  const t = (text || '').toLowerCase();
  return [
    'madarchod', 'bhenchod', 'behenchod', 'mc', 'bc', 'gandu', 'gaand',
    'chutiya', 'harami', 'bakchod', 'kutta', 'fuck you', 'fucker',
    'asshole', 'bastard', 'moron', 'stupid', 'loser'
  ].some((word) => t.includes(word));
}

function buildModerationReply(username, warningCount) {
  if (warningCount <= 1) {
    return `@${username || 'user'} warning 1/2: tone sambhal ke baat kar. Next time seedha action hoga.`;
  }
  return `@${username || 'user'} limit cross kar di. Group se hataaya ja raha hai.`;
}

async function runCommand(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args);
    const chunks = [];
    const errors = [];
    child.stdout.on('data', (d) => chunks.push(d));
    child.stderr.on('data', (d) => errors.push(d));
    child.on('close', (code) => {
      if (code === 0) return resolve(Buffer.concat(chunks));
      reject(new Error(Buffer.concat(errors).toString() || `${command} failed with code ${code}`));
    });
  });
}

function mediaIntent(text) {
  const t = (text || '').toLowerCase();
  if (/(extract audio|video to audio|convert .* to mp3|audio from video)/.test(t)) return 'extract-audio';
  if (/(convert .* to wav|make .* wav)/.test(t)) return 'to-wav';
  if (/(convert .* to mp3|make .* mp3)/.test(t)) return 'to-mp3';
  return null;
}

async function downloadTelegramFile(bot, fileId, extension = 'bin') {
  const fileLink = await bot.getFileLink(fileId);
  const response = await axios.get(fileLink, { responseType: 'arraybuffer', timeout: 60000 });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'keli-media-'));
  const inputPath = path.join(dir, `input.${extension}`);
  fs.writeFileSync(inputPath, Buffer.from(response.data));
  return { dir, inputPath };
}

async function convertMedia(inputPath, mode) {
  const dir = path.dirname(inputPath);
  const outputExt = mode === 'to-wav' ? 'wav' : 'mp3';
  const outputPath = path.join(dir, `output.${outputExt}`);
  const args = mode === 'to-wav'
    ? ['-y', '-i', inputPath, outputPath]
    : ['-y', '-i', inputPath, '-vn', '-acodec', 'libmp3lame', outputPath];
  await runCommand('ffmpeg', args);
  return outputPath;
}

async function sendConvertedMedia(bot, chatId, outputPath, mode) {
  const stream = fs.createReadStream(outputPath);
  if (mode === 'to-wav') {
    await telegramCall(() => bot.sendDocument(chatId, stream, {}, { filename: path.basename(outputPath), contentType: 'audio/wav' }));
  } else {
    await telegramCall(() => bot.sendAudio(chatId, stream, {}, { filename: path.basename(outputPath), contentType: 'audio/mpeg' }));
  }
}

async function handleMediaProcessing(bot, msg, text) {
  const intent = mediaIntent(text);
  const reply = msg.reply_to_message;
  if (!intent || !reply) return false;

  const video = reply.video || reply.document;
  const audio = reply.audio || reply.voice || reply.document;
  const source = video || audio;
  if (!source?.file_id) {
    await telegramCall(() => bot.sendMessage(msg.chat.id, 'Media convert karne ke liye kisi audio/video file par reply karke bolo.'));
    return true;
  }

  const ext = reply.video ? 'mp4' : (reply.audio?.file_name?.split('.').pop() || reply.document?.file_name?.split('.').pop() || 'bin');
  const { dir, inputPath } = await downloadTelegramFile(bot, source.file_id, ext);
  try {
    const outputPath = await convertMedia(inputPath, intent);
    await sendConvertedMedia(bot, msg.chat.id, outputPath, intent);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
  return true;
}

async function handleInviteRequest(bot, msg, text) {
  const normalized = (text || '').toLowerCase();
  if (!/(invite link|create invite|generate invite|\/invite)/.test(normalized)) {
    return false;
  }
  const trusted = getTrustedMemberIds();
  const memberId = String(msg.from?.id || '');
  const member = await bot.getChatMember(msg.chat.id, memberId).catch(() => null);
  const isAdmin = member && ['creator', 'administrator'].includes(member.status);
  if (!isAdmin && !trusted.has(memberId)) {
    await telegramCall(() => bot.sendMessage(msg.chat.id, 'Invite link sirf trusted members ya admins generate kar sakte hain.'));
    return true;
  }
  const invite = await telegramCall(() => bot.createChatInviteLink(msg.chat.id, { creates_join_request: false })).catch(() => null);
  if (!invite?.invite_link) {
    await telegramCall(() => bot.sendMessage(msg.chat.id, 'Invite link generate nahi ho paaya. Bot ko proper admin rights chahiye.'));
    return true;
  }
  await telegramCall(() => bot.sendMessage(msg.chat.id, `Invite link ready:\n${invite.invite_link}`));
  return true;
}

async function handleModeration(bot, msg, text) {
  if (!moderationEnabled()) return false;
  const chatType = msg.chat?.type;
  if (!['group', 'supergroup'].includes(chatType)) return false;
  if (!containsAbuse(text)) return false;

  const userId = String(msg.from?.id || '');
  const username = msg.from?.username || msg.from?.first_name || 'user';
  const warnings = (warningState.get(userId) || 0) + 1;
  warningState.set(userId, warnings);

  if (warnings >= 2) {
    await telegramCall(() => bot.banChatMember(msg.chat.id, userId)).catch(() => null);
    await telegramCall(() => bot.sendMessage(msg.chat.id, buildModerationReply(username, warnings)));
    await logModerationEvent({
      user_id: userId,
      username,
      platform: 'telegram',
      chat_id: String(msg.chat.id),
      action: 'kick',
      reason: 'Repeated abusive language',
    });
  } else {
    await telegramCall(() => bot.sendMessage(msg.chat.id, buildModerationReply(username, warnings)));
    await logModerationEvent({
      user_id: userId,
      username,
      platform: 'telegram',
      chat_id: String(msg.chat.id),
      action: 'warn',
      reason: 'Abusive language detected',
    });
  }
  return true;
}

async function startTelegramBot() {
  try {
    const config = await fetchBotConfig();
    currentConfig = config;
    const token = config?.telegram_token || process.env.TELEGRAM_BOT_TOKEN;

    if (isPlaceholderToken(token)) {
      console.log('⚠️  Telegram: No valid bot token set — skipping');
      return;
    }

    if (currentBot && token === currentToken) {
      console.log('ℹ️  Telegram: Bot already running with current token');
      return;
    }

    if (currentBot) {
      console.log('🔄 Telegram: Token changed, restarting bot...');
      currentBot.stopPolling();
    }

    currentToken = token;
    const bot = new TelegramBot(token, { polling: true });
    currentBot = bot;
    console.log(`✅ Telegram bot started (polling mode, token ${maskToken(token)})`);
    try {
      const me = await bot.getMe();
      currentBotUsername = me?.username || null;
      currentBotId = me?.id || null;
    } catch (err) {
      currentBotUsername = null;
      currentBotId = null;
    }

    bot.on('new_chat_members', async (msg) => {
      const members = msg.new_chat_members || [];
      const joinedSelf = members.find((member) => member.id === currentBotId || member.username === currentBotUsername);
      if (!joinedSelf) return;
      const groupName = msg.chat?.title || 'this group';
      await telegramCall(() => bot.sendMessage(msg.chat.id, `Welcome in group ${groupName}. MNI is now active here.`)).catch(() => null);
    });

    bot.on('message', async (msg) => {
      const chatId   = msg.chat.id;
      const text     = msg.text || '';
      const userId   = String(msg.from?.id || 'unknown');
      const username = msg.from?.username || msg.from?.first_name || 'User';

      if (!text || text.startsWith('/start')) {
        if (text?.startsWith('/start')) {
          await telegramCall(() => bot.sendMessage(chatId,
            `Hey ${username}! I'm MNI.\n\nAsk me anything, or try:\n• "generate image [description]"\n• "voice note [text]"\n• "male voice [text]"`
          ));
        }
        return;
      }

      try {
        if (await handleModeration(bot, msg, text)) return;
        if (await handleInviteRequest(bot, msg, text)) return;
        if (await handleMediaProcessing(bot, msg, text)) return;
        if (await handleN8nWorkflow(bot, msg, text)) return;
        if (!shouldReplyInChat(msg, text)) return;

        // Show typing indicator
        await telegramCall(() => bot.sendChatAction(chatId, 'typing'));

        const cleanText = stripBotMention(text);
        const res = await axios.post(`${BACKEND_URL}/api/chat/message`, {
          user_id:  userId,
          username: username,
          platform: 'telegram',
          chat_id: String(chatId),
          scope_type: msg.chat?.type === 'private' ? 'dm' : 'channel',
          message:  cleanText || text,
        }, { timeout: 30000 });

        const { response, type, status } = res.data;
        if (!response || String(status || '').toLowerCase() === 'silent') {
          return;
        }

        if (type === 'image' && typeof response === 'string' && response.startsWith('http')) {
          await telegramCall(() => bot.sendPhoto(chatId, response));
        } else if (type === 'image' && typeof response === 'string' && response.startsWith('data:image/')) {
          const imageBuffer = parseDataImageUri(response);
          if (!imageBuffer) {
            await telegramCall(() => bot.sendMessage(chatId, 'Image aayi thi but safe format me send nahi ho paayi.'));
            return;
          }
          await telegramCall(() => bot.sendPhoto(chatId, imageBuffer));
        } else if (type === 'voice' && typeof response === 'string' && response.startsWith('data:audio/')) {
          const audioBuffer = parseDataAudioUri(response);
          if (!audioBuffer) {
            await telegramCall(() => bot.sendMessage(chatId, 'Voice note bani thi but safe format me send nahi ho paayi.'));
            return;
          }
          await telegramCall(() => bot.sendVoice(chatId, audioBuffer));
        } else {
          await telegramCall(() => bot.sendMessage(chatId, response));
        }

      } catch (err) {
        console.error('[Telegram Error]', err.message);
        await telegramCall(() => bot.sendMessage(chatId, 'System is busy, try again later 😅')).catch(() => null);
      }
    });

    bot.on('polling_error', (err) => {
      console.error('[Telegram Polling Error]', err.message);
    });

    return bot;

  } catch (err) {
    console.error('❌ Fatal error starting Telegram bot:', err.message);
  }
}

// Try to start bot immediately, then check for token changes every 30 seconds
async function initTelegramBot() {
  await startTelegramBot();

  setInterval(async () => {
    const config = await fetchBotConfig();
    currentConfig = config;
    const token = config?.telegram_token || process.env.TELEGRAM_BOT_TOKEN;
    if (!isPlaceholderToken(token) && token !== currentToken) {
      console.log('🔄 Telegram: New token detected, restarting bot...');
      await startTelegramBot();
    }
  }, 30000);
}

module.exports = { startTelegramBot, initTelegramBot };
