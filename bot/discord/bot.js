/**
 * MNI Automation Manager - Discord Bot
 */
const { Client, GatewayIntentBits, Events, Partials } = require('discord.js');
const axios = require('axios');
const fs = require('fs');
const path = require('path');

const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:5000';
const BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || process.env.ADMIN_SECRET_KEY || '';
const BOT_REPLY_COOLDOWN_MS = 60000;
const ENABLE_DISCORD_MEMBER_WELCOME = String(process.env.DISCORD_MEMBER_WELCOME || 'false').toLowerCase() === 'true';
const ENABLE_DISCORD_MESSAGE_CONTENT = String(process.env.DISCORD_MESSAGE_CONTENT || 'false').toLowerCase() === 'true';
const DISCORD_DEBUG_LOG = path.join(__dirname, '../../instance/discord_runtime.log');

let currentClient = null;
let currentToken = null;
let currentConfig = null;
const recentBotReplies = new Map();
const warningState = new Map();

function logRuntime(event, meta = {}) {
  const payload = { event, ...meta };
  console.log(`[Discord Debug] ${event}`, meta);
  try {
    fs.mkdirSync(path.dirname(DISCORD_DEBUG_LOG), { recursive: true });
    fs.appendFileSync(DISCORD_DEBUG_LOG, `${new Date().toISOString()} ${JSON.stringify(payload)}\n`);
  } catch (_) {
    // Best-effort debug logging only.
  }
}

function isPlaceholderToken(token) {
  return !token || token.includes('your_');
}

function stripBotMention(content, botId) {
  return (content || '')
    .replace(new RegExp(`<@!?${botId}>`, 'g'), '')
    .trim();
}

function isReplyToBot(message, client) {
  return Boolean(
    message.reference?.messageId &&
    message.mentions?.repliedUser?.id === client.user.id
  );
}

function containsAbuse(text) {
  const t = (text || '').toLowerCase();
  return [
    'mc', 'bc', 'madarchod', 'bhenchod', 'behenchod', 'gandu', 'gaand',
    'chutiya', 'harami', 'kutta', 'bakchod', 'idiot', 'stupid', 'moron',
    'fuck you', 'fucker', 'bastard', 'asshole', 'loser'
  ].some((word) => t.includes(word));
}

function getTrustedMemberIds() {
  const raw = currentConfig?.trusted_member_ids || '';
  return new Set(
    String(raw)
      .split(',')
      .map((v) => v.trim())
      .filter(Boolean)
  );
}

function moderationEnabled() {
  return String(currentConfig?.moderation_enabled || 'true').toLowerCase() === 'true';
}

function buildModerationReply(username, warningCount) {
  if (warningCount <= 1) {
    return `@${username || 'user'} warning 1/2: tone sambhal ke baat kar. Next time action hoga.`;
  }
  return `@${username || 'user'} limit cross kar di. Channel safety ke liye action liya ja raha hai.`;
}

function isExplicitCommand(text) {
  return /^\/(start|help|invite|form|book|cancel)\b/i.test(text || '');
}

function buildAttitudeReply(username, isBotAuthor) {
  const name = username || 'buddy';
  if (isBotAuthor) {
    return `@${name} clear message bhejo, tab main sahi help kar paunga.`;
  }
  return `@${name} please apna request clearly bhejo.`;
}

function shouldThrottleBotReply(authorId) {
  const now = Date.now();
  const last = recentBotReplies.get(authorId) || 0;
  if (now - last < BOT_REPLY_COOLDOWN_MS) {
    return true;
  }
  recentBotReplies.set(authorId, now);
  return false;
}

function parseDataImageUri(uri) {
  const match = /^data:(image\/[a-zA-Z0-9.+-]+);base64,(.+)$/.exec(uri || '');
  if (!match) {
    return null;
  }
  const extension = match[1].split('/')[1] || 'png';
  return {
    buffer: Buffer.from(match[2], 'base64'),
    name: `generated-image.${extension}`,
  };
}

function parseDataAudioUri(uri) {
  const match = /^data:audio\/([a-zA-Z0-9.+-]+);base64,(.+)$/.exec(uri || '');
  if (!match) {
    return null;
  }
  const extension = match[1].split('/')[1] || 'mp3';
  return {
    buffer: Buffer.from(match[2], 'base64'),
    name: `voice-note.${extension}`,
  };
}

async function syncDiscordInboundMessage(message, platform = 'discord') {
  if (!BOT_SYNC_SECRET || !message?.content?.trim()) {
    logRuntime('inbound_sync_skipped', {
      reason: !BOT_SYNC_SECRET ? 'missing_sync_secret' : 'empty_content',
      channelId: message?.channel?.id || '',
      authorId: message?.author?.id || '',
    });
    return;
  }
  try {
    await axios.post(`${BACKEND_URL}/api/chat/platform-log`, {
      user_id: message.author?.id || 'unknown',
      username: message.author?.username || 'User',
      platform,
      chat_id: message.channel?.id ? String(message.channel.id) : '',
      message: message.content.trim(),
      status: 'seen',
      api_used: 'discord_inbound_sync',
    }, {
      headers: { 'X-Bot-Admin-Token': BOT_SYNC_SECRET },
      timeout: 5000,
    });
    logRuntime('inbound_sync_ok', {
      channelId: message.channel?.id || '',
      authorId: message.author?.id || '',
      contentLen: message.content.trim().length,
    });
  } catch (err) {
    logRuntime('inbound_sync_error', {
      message: err.message,
      channelId: message?.channel?.id || '',
      authorId: message?.author?.id || '',
    });
  }
}

async function handleInviteRequest(message, text) {
  const normalized = (text || '').toLowerCase();
  if (!/(invite link|create invite|generate invite|\/invite)/.test(normalized)) {
    return false;
  }
  if (!message.guild) {
    await message.reply('Invite link server channel me hi generate hota hai.');
    return true;
  }

  const trusted = getTrustedMemberIds();
  const isAdmin = Boolean(message.member?.permissions?.has('CreateInstantInvite') || message.member?.permissions?.has('Administrator'));
  if (!isAdmin && !trusted.has(String(message.author?.id || ''))) {
    await message.reply('Invite link sirf trusted members ya admins generate kar sakte hain.');
    return true;
  }

  try {
    const invite = await message.channel.createInvite({
      maxAge: 0,
      maxUses: 0,
      unique: true,
      reason: `Invite requested by ${message.author?.username || 'unknown'}`,
    });
    await message.reply(`Invite link ready:\n${invite.url}`);
  } catch (err) {
    await message.reply('Invite link generate nahi ho paaya. Bot ko proper permissions chahiye.');
  }
  return true;
}

async function handleModeration(message, text) {
  if (!moderationEnabled() || !message.guild || !containsAbuse(text)) {
    return false;
  }

  const userId = String(message.author?.id || '');
  const username = message.author?.username || 'user';
  const warnings = (warningState.get(userId) || 0) + 1;
  warningState.set(userId, warnings);

  await message.reply(buildModerationReply(username, warnings));
  if (warnings < 2) {
    return true;
  }

  try {
    if (message.member?.moderatable) {
      await message.member.timeout(10 * 60 * 1000, 'Repeated abusive language');
    }
  } catch (err) {
    console.log(`⚠️  Discord: moderation action skipped (${err.message})`);
  }
  return true;
}

function resolveWelcomeChannel(guild) {
  if (guild?.systemChannel && guild.systemChannel.permissionsFor(guild.members.me)?.has('SendMessages')) {
    return guild.systemChannel;
  }
  return guild?.channels?.cache?.find((channel) =>
    channel?.isTextBased?.() &&
    channel.permissionsFor(guild.members.me)?.has('SendMessages')
  ) || null;
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
    logRuntime('config_sync_ok', {
      hasDiscordToken: !!res.data?.discord_token,
      backendUrl: BACKEND_URL,
    });
    return res.data || null;
  } catch (err) {
    logRuntime('config_sync_error', { message: err.message, backendUrl: BACKEND_URL });
    return null;
  }
}

async function startDiscordBot() {
  const config = await fetchBotConfig();
  currentConfig = config;
  const token = config?.discord_token || process.env.DISCORD_BOT_TOKEN;
  if (isPlaceholderToken(token)) {
    logRuntime('startup_skipped', { reason: 'missing_token' });
    return;
  }

  if (currentClient && token === currentToken) {
    console.log('ℹ️  Discord: Bot already running with current token');
    return currentClient;
  }

  if (currentClient) {
    console.log('🔄 Discord: Token changed, restarting bot...');
    currentClient.destroy();
  }

  const intents = [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.DirectMessages,
  ];
  if (ENABLE_DISCORD_MESSAGE_CONTENT) {
    intents.push(GatewayIntentBits.MessageContent);
  }
  if (ENABLE_DISCORD_MEMBER_WELCOME) {
    intents.push(GatewayIntentBits.GuildMembers);
  }
  const client = new Client({
    intents,
    partials: [Partials.Channel],
  });

  client.once(Events.ClientReady, (c) => {
    console.log(`✅ Discord bot ready as ${c.user.tag}`);
    logRuntime('ready', {
      user: c.user.tag,
      intents,
      guilds: c.guilds.cache.map((guild) => ({ id: guild.id, name: guild.name })).slice(0, 50),
    });
  });

  client.on('raw', (packet) => {
    if (!packet || !['MESSAGE_CREATE', 'MESSAGE_UPDATE', 'INTERACTION_CREATE'].includes(packet.t)) {
      return;
    }
    const data = packet.d || {};
    logRuntime('raw_event', {
      type: packet.t,
      channelId: data.channel_id || '',
      guildId: data.guild_id || '',
      authorId: data.author?.id || '',
      contentPreview: String(data.content || '').slice(0, 120),
    });
  });

  if (ENABLE_DISCORD_MEMBER_WELCOME) {
    client.on(Events.GuildMemberAdd, async (member) => {
      const channel = resolveWelcomeChannel(member.guild);
      if (!channel) return;
      try {
        await channel.send(`Welcome ${member.displayName || member.user.username}! MNI is now here to help you in ${member.guild.name}.`);
      } catch (err) {
        console.log(`⚠️  Discord: welcome message skipped (${err.message})`);
      }
    });
  }

  client.on(Events.MessageCreate, async (message) => {
    if (message.author?.bot) {
      logRuntime('ignore_bot_message', {
        channelId: message.channel?.id || '',
        authorId: message.author?.id || '',
        author: message.author?.username || '',
      });
      return;
    }

    // Only respond when mentioned, replied to, explicit command, OR in DM
    const rawContent = String(message.content || message.cleanContent || '').trim();
    const isDM       = message.channel.type === 1;
    const isMentioned= message.mentions.has(client.user);
    const isReply    = isReplyToBot(message, client);
    const explicitCommand = isExplicitCommand(rawContent);
    logRuntime('message_event', {
      channelId: message.channel?.id || '',
      guildId: message.guild?.id || '',
      authorId: message.author?.id || '',
      author: message.author?.username || '',
      isDM,
      isMentioned,
      isReply,
      explicitCommand,
      hasContent: !!rawContent,
      contentPreview: rawContent.slice(0, 120),
    });
    if (!rawContent && !isDM && !isMentioned && !isReply) return;

    if (rawContent && !isDM && !isMentioned && !isReply) {
      await syncDiscordInboundMessage(message);
      if (!explicitCommand) return;
    }
    if (!isDM && !isMentioned && !isReply && !explicitCommand) return;

    const text     = stripBotMention(rawContent, client.user.id);
    const userId   = message.author.id;
    const username = message.author.username;

    try {
      await message.channel.sendTyping();

      if (!text && (isMentioned || isReply || isDM)) {
        if (isDM) {
          await message.reply('DM me mention ki zarurat nahi hoti. Bas apna message plain text me bhejo.');
        } else {
          await message.reply('Message dikh gaya, but content empty aa raha hai. Mention ke saath actual text bhi bhejo.');
        }
        return;
      }

      if (await handleModeration(message, text)) return;
      if (await handleInviteRequest(message, text)) return;

      if (containsAbuse(text)) {
        await message.reply(buildAttitudeReply(username, false));
        return;
      }

      const res = await axios.post(`${BACKEND_URL}/api/chat/message`, {
        user_id:  userId,
        username: username,
        platform: 'discord',
        chat_id:  String(message.channel.id || ''),
        scope_type: isDM ? 'dm' : 'channel',
        message:  text || 'Hello',
      }, { timeout: 30000 });
      logRuntime('backend_reply_ok', {
        channelId: message.channel?.id || '',
        authorId: userId,
        apiUsed: res.data?.api_used || '',
        type: res.data?.type || '',
      });

      const { response, type, status } = res.data;
      if (!response || ['silent'].includes(String(status || '').toLowerCase())) {
        logRuntime('silent_skip', {
          channelId: message.channel?.id || '',
          authorId: userId,
          status: status || '',
        });
        return;
      }

      if (type === 'image' && typeof response === 'string' && response.startsWith('http')) {
        await message.reply({ content: '🖼️ Here you go!', files: [response] });
      } else if (type === 'image' && typeof response === 'string' && response.startsWith('data:image/')) {
        const image = parseDataImageUri(response);
        if (!image) {
          await message.reply('Image aayi thi but format safe nahi tha, isliye send nahi ki.');
          return;
        }
        await message.reply({ content: '🖼️ Here you go!', files: [{ attachment: image.buffer, name: image.name }] });
      } else if (type === 'voice' && typeof response === 'string' && response.startsWith('data:audio/')) {
        const audio = parseDataAudioUri(response);
        if (!audio) {
          await message.reply('Voice note bani thi but format safe nahi tha, isliye send nahi ki.');
          return;
        }
        await message.reply({ files: [{ attachment: audio.buffer, name: audio.name }] });
      } else {
        // Discord has 2000 char limit
        const chunks = response.match(/.{1,1900}/gs) || [response];
        for (const chunk of chunks) {
          await message.reply(chunk);
        }
      }

    } catch (err) {
      console.error('[Discord Error]', err.message);
      logRuntime('backend_reply_error', {
        channelId: message.channel?.id || '',
        authorId: userId,
        message: err.message,
      });
      await message.reply('System is busy, try again later 😅');
    }
  });

  client.login(token).catch(err => {
    console.error('[Discord Login Error]', err.message);
    logRuntime('login_error', { message: err.message });
  });

  currentClient = client;
  currentToken = token;
  return client;
}

function initDiscordBot() {
  startDiscordBot();

  setInterval(async () => {
    const config = await fetchBotConfig();
    currentConfig = config;
    const token = config?.discord_token || process.env.DISCORD_BOT_TOKEN;
    if (!isPlaceholderToken(token) && token !== currentToken) {
      await startDiscordBot();
    }
  }, 30000);
}

module.exports = { startDiscordBot, initDiscordBot };
