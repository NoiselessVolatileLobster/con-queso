import discord
import random
import asyncio
import re
import datetime
import json
import os
from typing import Optional, Literal, Dict, List
from collections import Counter
import math

from redbot.core import commands, Config, bank, checks
from redbot.core.data_manager import bundled_data_path
from redbot.core.utils.chat_formatting import pagify, box
from tabulate import tabulate

class Gortle(commands.Cog):
    """A communal 6-letter Wordle-style game for Discord."""
    
    # Configuration for Emoji colors
    EMOJI_UNUSED = "white"   # Letters that have not been guessed
    EMOJI_CORRECT = "green"  # Guessed and in right position
    EMOJI_PRESENT = "yellow" # Guessed and in wrong position
    EMOJI_ABSENT = "grey"    # Guessed and NOT in the word (inferred)
    
    MAX_GUESSES = 9
    CREDITS_PER_POINT = 10

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8473629103, force_registration=True)

        # Initialize lists
        self.solutions = []
        self.guesses = []
        
        # Caches
        self.emoji_cache: Dict[str, int] = {} # Synced Guild Emojis (ID based)
        self.app_emoji_cache: List[discord.Emoji] = [] # Application Emojis (Object based)
        
        self._load_word_lists()

        default_global = {
            "schedule_auto_freq": 0, # Times per hour (0 to disable)
            "schedule_manual_max": 0, # Max manual games per clock hour (0 for unlimited)
            "manual_log": {"hour": -1, "count": 0}, # Tracks manual usage: {hour: int, count: int}
            "next_game_timestamp": 0,
            "current_game_id": 0,
            "current_word": None,
            "used_words": [],
            "game_number": 0,
            "game_active": False,
            "cooldown_reset_timestamp": 0, 
            "consecutive_no_guesses": 0, 
            "game_state": {
                "solved_indices": [],
                "found_letters": [], 
                "guessed_letters": [], 
                "guesses_made": 0,
                "history": [], 
                "round_scores": {} 
            },
            "win_amount": 100, 
            "weekly_role_id": None,
            "weekly_role_day": 0,
            "weekly_role_hour": 9,
            "last_weekly_award": 0,
            "emoji_map": {}, # Stores "name" -> emoji_id
            "suggested_words": []
        }

        default_guild = {
            "channel_id": None,
            "mention_role": None,
            "cooldown_seconds": 60,
            "thumbnail_url": None 
        }

        default_member = {
            "score": 0,
            "weekly_score": 0,
            "words_guessed": {},
            "last_guess_time": 0
        }

        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

        self.game_loop_task = self.bot.loop.create_task(self.game_loop())
        
        # Start tasks to load caches
        self.bot.loop.create_task(self._load_emoji_cache())
        self.bot.loop.create_task(self._fetch_app_emojis())
        
        self.lock = asyncio.Lock()

    def cog_unload(self):
        if self.game_loop_task:
            self.game_loop_task.cancel()

    async def _load_emoji_cache(self):
        """Loads the guild emoji map from config into memory."""
        self.emoji_cache = await self.config.emoji_map()

    async def _fetch_app_emojis(self):
        """Fetches emojis uploaded directly to the Discord Application."""
        try:
            # wait_until_ready is crucial ensuring the bot is connected before API calls
            await self.bot.wait_until_ready()
            self.app_emoji_cache = await self.bot.fetch_application_emojis()
        except Exception as e:
            # Silent failure expected if bot is not part of a team/application context with emojis
            pass

    def _load_word_lists(self):
        """Loads words from JSON files in the data directory."""
        data_path = bundled_data_path(self)
        
        try:
            with open(data_path / "solutions.json", "r", encoding="utf-8") as f:
                self.solutions = json.load(f)
            
            with open(data_path / "guesses.json", "r", encoding="utf-8") as f:
                raw_guesses = json.load(f)
                
            combined = set(raw_guesses + self.solutions)
            self.guesses = list(combined)
            print(f"[Gortle] Loaded {len(self.solutions)} solutions and {len(self.guesses)} valid guesses.")
            
        except FileNotFoundError:
            print(f"[Gortle] WARNING: Word lists not found in {data_path}. Using fallback list.")
            # Fallback for testing/initial install
            self.solutions = ["gortle", "orange", "purple", "banana", "castle", "dragon", "spirit", "botany"]
            self.guesses = self.solutions.copy()
        except json.JSONDecodeError as e:
            print(f"[Gortle] Error parsing JSON: {e}")
            self.solutions = ["failed"]
            self.guesses = ["failed"]

    def _find_emoji(self, name_query: str) -> Optional[discord.Emoji]:
        """
        Search for an emoji.
        Priority 1: Application Emojis (Uploaded to Dev Portal).
        Priority 2: Synced Emoji ID (from [p]gortleset syncemojis).
        Priority 3: Global search of all emojis the bot can see.
        """
        target = name_query.lower()

        # 1. Priority: Application Emojis
        for emoji in self.app_emoji_cache:
            if emoji.name.lower() == target:
                return emoji

        # 2. Priority: Synced ID Cache
        if target in self.emoji_cache:
            emoji_id = self.emoji_cache[target]
            emoji = self.bot.get_emoji(emoji_id)
            if emoji:
                return emoji

        # 3. Fallback: Search all guild emojis
        for emoji in self.bot.emojis:
            if emoji.name.lower() == target:
                return emoji
        return None

    def _get_emoji_str(self, char: str, color: str) -> str:
        """Helper to format the emoji string."""
        emoji_name = f"{color}{char.lower()}"
        emoji = self._find_emoji(emoji_name)
        if emoji:
            return str(emoji)
        return f":{emoji_name}:"

    def _get_keyboard_visual(self, state, solution) -> str:
        """Generates the QWERTY keyboard visual based on game state."""
        rows = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]
        visual_rows = []

        guessed_letters = set(state['guessed_letters'])
        letter_status = {}
        
        for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ".lower():
            if char not in guessed_letters:
                letter_status[char] = self.EMOJI_UNUSED
            else:
                if char not in solution:
                    letter_status[char] = self.EMOJI_ABSENT
                else:
                    is_solved = False
                    for idx in state['solved_indices']:
                        if solution[idx] == char:
                            is_solved = True
                            break
                    
                    if is_solved:
                        letter_status[char] = self.EMOJI_CORRECT
                    else:
                        letter_status[char] = self.EMOJI_PRESENT
        
        spacer = self._find_emoji("greysquare")
        spacer_str = str(spacer) if spacer else ":greysquare:"

        for i, row in enumerate(rows):
            line = ""
            if i == 2:
                line += f"{spacer_str}"

            for char in row.lower():
                color = letter_status.get(char, self.EMOJI_UNUSED)
                line += self._get_emoji_str(char, color)
            
            if i == 1:
                line += f"{spacer_str}"
            if i == 2:
                line += f"{spacer_str}{spacer_str}"

            visual_rows.append(line)
            
        return "\n".join(visual_rows)

    def _calculate_next_auto_time(self, now, freq):
        if freq <= 0:
            return 0
            
        interval_minutes = 60 / freq
        current_minute = now.minute
        
        next_slot_index = math.ceil((current_minute + 1) / interval_minutes) 
        next_minute = int(next_slot_index * interval_minutes)
        
        if next_minute >= 60:
            next_time = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
        else:
            next_time = now.replace(minute=next_minute, second=0, microsecond=0)
            
        return int(next_time.timestamp())

    async def game_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                now = datetime.datetime.now(datetime.timezone.utc)
                timestamp = int(now.timestamp())

                await self.check_weekly_role(now)

                next_game_ts = await self.config.next_game_timestamp()
                auto_freq = await self.config.schedule_auto_freq()
                sleep_streak = await self.config.consecutive_no_guesses()
                
                # Check for scheduled games
                if auto_freq > 0 and sleep_streak < 3:
                    if next_game_ts == 0 or timestamp >= next_game_ts:
                        new_next_ts = self._calculate_next_auto_time(now, auto_freq)
                        await self.config.next_game_timestamp.set(new_next_ts)

                        # Start game if this isn't the first initialization (next_game_ts != 0)
                        if next_game_ts != 0:
                            await self.start_new_game(manual=False)

            except Exception as e:
                print(f"[Gortle] Error in loop: {e}")
            
            await asyncio.sleep(60)

    async def start_new_game(self, manual=False, channel_ctx=None):
        """
        Starts a new game.
        manual: Bool, True if started by a user (via 'wake up' or similar).
        channel_ctx: Optional[discord.TextChannel], used to send feedback if manual limit reached.
        """
        async with self.lock:
            guild_config = await self.config.all_guilds()
            target_channel = None
            
            # Find the configured channel
            for gid, data in guild_config.items():
                if data['channel_id']:
                    g = self.bot.get_guild(gid)
                    if g:
                        c = g.get_channel(data['channel_id'])
                        if c:
                            target_channel = c
                            break
            
            if not target_channel:
                if manual and channel_ctx:
                    await channel_ctx.send("Gortle channel not configured or not found.")
                return

            # --- Check Manual Schedule Limits ---
            if manual:
                manual_max = await self.config.schedule_manual_max()
                if manual_max > 0:
                    current_hour = datetime.datetime.now(datetime.timezone.utc).hour
                    manual_log = await self.config.manual_log()
                    
                    # Reset log if hour changed
                    if manual_log['hour'] != current_hour:
                        manual_log = {'hour': current_hour, 'count': 0}
                    
                    if manual_log['count'] >= manual_max:
                        if channel_ctx:
                            await channel_ctx.send(f"Wait a bit! The manual game limit ({manual_max}/hr) has been reached.")
                        return
                    
                    # Increment count
                    manual_log['count'] += 1
                    await self.config.manual_log.set(manual_log)
            # -------------------------------------

            await self.config.cooldown_reset_timestamp.set(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))

            active = await self.config.game_active()
            old_word = await self.config.current_word()
            
            # Handle previous game expiration
            if active and old_word:
                embed = discord.Embed(title="Gortle Expired!", description=f"The word was **{old_word.upper()}**.", color=discord.Color.red())
                thumb = await self.config.guild(target_channel.guild).thumbnail_url()
                if thumb:
                    embed.set_thumbnail(url=thumb)
                await target_channel.send(embed=embed)

                state = await self.config.game_state()
                history = state.get("history", [])
                
                # Sleep logic
                if len(history) == 0:
                    current_streak = await self.config.consecutive_no_guesses() + 1
                    await self.config.consecutive_no_guesses.set(current_streak)
                    
                    if current_streak >= 3 and not manual:
                        sleep_embed = discord.Embed(
                            title="Gortle has gone to sleep", 
                            description=f"Three games with no guesses. Zzz...\nSay `wake up` to wake me up!",
                            color=discord.Color.dark_grey()
                        )
                        if thumb:
                            sleep_embed.set_thumbnail(url=thumb)
                            
                        await target_channel.send(embed=sleep_embed)
                        
                        await self.config.next_game_timestamp.set(0)
                        await self.config.game_active.set(False)
                        return
                else:
                    await self.config.consecutive_no_guesses.set(0)

            if manual:
                await self.config.consecutive_no_guesses.set(0)

            # Pick word
            used = await self.config.used_words()
            available = [w for w in self.solutions if w not in used and len(w) == 6]
            
            if not available:
                used = []
                await self.config.used_words.set([])
                available = [w for w in self.solutions if len(w) == 6]

            if not available:
                if manual and channel_ctx:
                    await channel_ctx.send("No valid 6-letter words available in dictionary!")
                print("[Gortle] No valid 6-letter words available in solutions.json!")
                return

            new_word = random.choice(available)
            
            async with self.config.used_words() as u:
                u.append(new_word)

            game_num = await self.config.game_number() + 1
            await self.config.game_number.set(game_num)
            await self.config.current_word.set(new_word)
            await self.config.game_active.set(True)
            
            new_state = {
                "solved_indices": [],
                "found_letters": [],
                "guessed_letters": [],
                "guesses_made": 0,
                "history": [],
                "round_scores": {}
            }
            await self.config.game_state.set(new_state)

            role_id = await self.config.guild(target_channel.guild).mention_role()
            mention = f"<@&{role_id}>" if role_id else ""
            
            keyboard_view = self._get_keyboard_visual(new_state, new_word)

            next_ts = await self.config.next_game_timestamp()
            now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            
            if next_ts > now_ts:
                 keyboard_view += f"\n\n**Next Game:** <t:{next_ts}:R>"
            
            desc = "Guess the 6-letter word by typing `!word`!"
            
            embed = discord.Embed(title=f"New Gortle Started! (#{game_num})", description=desc, color=discord.Color.green())
            embed.add_field(name="\u200b", value=keyboard_view, inline=False)
            
            thumb = await self.config.guild(target_channel.guild).thumbnail_url()
            if thumb:
                embed.set_thumbnail(url=thumb)
                
            await target_channel.send(
                content=mention, 
                embed=embed, 
                allowed_mentions=discord.AllowedMentions(roles=True)
            )

    async def check_weekly_role(self, now):
        target_day = await self.config.weekly_role_day()
        target_hour = await self.config.weekly_role_hour()
        last_award = await self.config.last_weekly_award()
        role_id = await self.config.weekly_role_id()

        if not role_id:
            return

        if now.weekday() == target_day and now.hour >= target_hour:
            # Check if ~138 hours have passed (slightly less than a week to ensure it triggers)
            if (now.timestamp() - last_award) > 500000: 
                await self.award_weekly_role(role_id)
                await self.config.last_weekly_award.set(int(now.timestamp()))

    async def award_weekly_role(self, role_id):
        members = await self.config.all_members()
        guild_config = await self.config.all_guilds()
        target_channel = None
        for gid, data in guild_config.items():
            if data['channel_id']:
                g = self.bot.get_guild(gid)
                if g:
                    target_channel = g.get_channel(data['channel_id'])
                    break
        
        if not target_channel:
            return

        role = target_channel.guild.get_role(role_id)
        if not role:
            return

        for member in role.members:
            try:
                await member.remove_roles(role, reason="Gortle weekly reset")
            except discord.Forbidden:
                pass

        top_scorer = None
        top_score = -1
        
        for g_id, m_data in members.items():
            for m_id, stats in m_data.items():
                if stats['weekly_score'] > top_score:
                    top_score = stats['weekly_score']
                    top_scorer = target_channel.guild.get_member(m_id)

        if top_scorer and top_score > 0:
            try:
                await top_scorer.add_roles(role, reason="Gortle weekly winner")
                # Fixed: Removed corrupted Chinese characters
                await target_channel.send(f":tada: **{top_scorer.mention}** is the Gortle Champion of the week with {top_score} points! :tada:")
            except discord.Forbidden:
                await target_channel.send("I tried to give the weekly role but lack permissions.")
        
        # Reset weekly scores
        for g_id, m_data in members.items():
            for m_id, stats in m_data.items():
                await self.config.member_from_ids(g_id, m_id).weekly_score.set(0)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if not message.guild:
            return

        conf_channel = await self.config.guild(message.guild).channel_id()
        if not conf_channel or message.channel.id != conf_channel:
            return

        content = message.content.lower().strip()

        # Improved wake up detection
        if content.startswith("wake up"):
            is_active = await self.config.game_active()
            sleep_streak = await self.config.consecutive_no_guesses()
            
            if not is_active and sleep_streak >= 3:
                await message.channel.send("I'm awake! Checking schedule and starting...")
                await self.start_new_game(manual=True, channel_ctx=message.channel)
                return

        # Note: We enforce '!' for game commands to avoid spam in chat
        if not content.startswith("!"):
            return

        guess = content[1:]
        if not re.fullmatch(r"[a-z]{6}", guess):
            return 
        
        if not await self.config.game_active():
            return
            
        cooldown = await self.config.guild(message.guild).cooldown_seconds()
        last_guess = await self.config.member(message.author).last_guess_time()
        reset_ts = await self.config.cooldown_reset_timestamp()
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        
        # If game reset recently, reset user cooldown
        if last_guess < reset_ts:
            last_guess = 0
        
        if (now - last_guess) < cooldown:
            if message.channel.permissions_for(message.guild.me).manage_messages:
                try:
                    await message.delete()
                except discord.NotFound:
                    pass
            
            next_time = int(last_guess + cooldown)
            remaining = int(next_time - now)
            await message.channel.send(f"{message.author.mention}, you need to wait. ({remaining}s left) Next guess: <t:{next_time}:R>", delete_after=5)
            return

        if guess not in self.guesses:
            prefixes = await self.bot.get_valid_prefixes(message.guild)
            prefix = prefixes[0] if prefixes else "!"
            await message.channel.send(
                f"I do not think that word is in my dictionary. You can suggest it using `{prefix}gortle addword {guess}`", 
                delete_after=10
            )
            return

        async with self.lock:
            if not await self.config.game_active():
                await message.channel.send("The game has already ended.", delete_after=5)
                return

            state = await self.config.game_state()
            history = state.get("history", [])
            
            # Check for duplicate guesses in the current game
            if any(entry.get("word") == guess for entry in history):
                await message.channel.send("That word has already been guessed.", delete_after=5)
                return

            # Valid new guess: Update timer now
            await self.config.member(message.author).last_guess_time.set(int(now))

            await self.process_guess(message, guess)

    async def process_guess(self, message, guess):
        solution = await self.config.current_word()
        state = await self.config.game_state()
        solved_indices = set(state['solved_indices'])
        
        # Count letters that are already "locked in" (Green)
        locked_chars = Counter()
        for idx in solved_indices:
            locked_chars[solution[idx]] += 1

        matched_in_guess = Counter()
        
        guess_visual = [""] * 6
        points = 0
        
        sol_chars = list(solution)
        guess_chars = list(guess)
        sol_remaining = list(solution) 
        
        # First Pass: Check for Greens (Correct Position)
        for i, char in enumerate(guess_chars):
            if char == sol_chars[i]:
                guess_visual[i] = self._get_emoji_str(char, self.EMOJI_CORRECT)
                sol_remaining[i] = None 
                matched_in_guess[char] += 1
                
                if i not in solved_indices:
                    # New Green discovery
                    k = matched_in_guess[char]
                    total_known = state['found_letters'].count(char)
                    locked_count = locked_chars[char]
                    floating_known = max(0, total_known - locked_count)
                    
                    if floating_known >= k:
                        points += 1 # Upgrade yellow to green
                    else:
                        points += 2 # Brand new letter found
                        state['found_letters'].append(char)
                    
                    state['solved_indices'].append(i)
                else:
                    points += 0 # Already known green

        # Second Pass: Check for Yellows (Wrong Position)
        for i, char in enumerate(guess_chars):
            if guess_visual[i] != "": continue

            if char in sol_remaining:
                guess_visual[i] = self._get_emoji_str(char, self.EMOJI_PRESENT)
                sol_remaining[sol_remaining.index(char)] = None 
                
                matched_in_guess[char] += 1
                k = matched_in_guess[char]
                current_known = state['found_letters'].count(char)
                
                if current_known >= k:
                    points += 0 # Already known this letter exists
                else:
                    points += 1 # New letter discovery (Yellow)
                    state['found_letters'].append(char)
            else:
                guess_visual[i] = self._get_emoji_str(char, self.EMOJI_ABSENT)

        history_entry = {
            "visual": ' '.join(guess_visual),
            "user_id": message.author.id,
            "word": guess
        }
        if 'history' not in state:
            state['history'] = []
        state['history'].append(history_entry)

        current_guessed = set(state['guessed_letters'])
        for c in guess:
            current_guessed.add(c)
        state['guessed_letters'] = sorted(list(current_guessed))
        
        round_scores = state.get('round_scores', {})
        str_uid = str(message.author.id)
        round_scores[str_uid] = round_scores.get(str_uid, 0) + points
        state['round_scores'] = round_scores

        await self.config.game_state.set(state)

        async with self.config.member(message.author).words_guessed() as wg:
            wg[guess] = wg.get(guess, 0) + 1
        
        if points > 0:
            await self.config.member(message.author).score.set(
                await self.config.member(message.author).score() + points
            )
            await self.config.member(message.author).weekly_score.set(
                await self.config.member(message.author).weekly_score() + points
            )

        game_num = await self.config.game_number()
        keyboard_view = self._get_keyboard_visual(state, solution)

        next_ts = await self.config.next_game_timestamp()
        now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        if next_ts > now_ts:
             keyboard_view += f"\n\n**Next Game:** <t:{next_ts}:R>"
        
        full_history = state['history']
        display_history = full_history if len(full_history) < 20 else full_history[-20:]
        
        history_lines = []
        for idx, entry in enumerate(display_history, 1):
             actual_idx = idx if len(full_history) < 20 else (len(full_history) - 20 + idx)
             user = message.guild.get_member(entry['user_id'])
             user_name = user.display_name if user else "Unknown"
             history_lines.append(f"`{actual_idx}.` {entry['visual']} (**{user_name}**)")

        description = "\n".join(history_lines)
        if len(full_history) >= 20:
             description = f"*(Previous guesses hidden)*\n{description}"

        embed = discord.Embed(title=f"Gortle #{game_num}", description=description, color=discord.Color.blue())
        
        total_round_points = round_scores.get(str_uid, 0)
        
        embed.add_field(name="Points Gained", value=f"+{points} ({total_round_points} points this round)", inline=True)
        embed.add_field(name="\u200b", value=keyboard_view, inline=False)
        
        thumb = await self.config.guild(message.guild).thumbnail_url()
        if thumb:
            embed.set_thumbnail(url=thumb)

        await message.channel.send(embed=embed)

        if guess == solution:
            await self.handle_win(message.author, message.channel, game_num)
        elif len(state['history']) >= self.MAX_GUESSES:
            # Game Over - Loss
            await self.handle_loss(message.channel, solution)

    async def handle_win(self, winner, channel, game_num):
        await self.config.consecutive_no_guesses.set(0)
        await self.config.game_active.set(False)
        
        state = await self.config.game_state()
        round_scores = state.get('round_scores', {})
        participants = set()
        
        for entry in state.get('history', []):
            participants.add(entry['user_id'])
            
        results = []
        
        for uid in participants:
            member = channel.guild.get_member(uid)
            if not member:
                continue
            
            await self.config.member(member).score.set(
                await self.config.member(member).score() + 2
            )
            await self.config.member(member).weekly_score.set(
                await self.config.member(member).weekly_score() + 2
            )
            
            guess_points = round_scores.get(str(uid), 0)
            total_round_points = guess_points + 2
            results.append((total_round_points, member))
            
            credits_to_give = total_round_points * self.CREDITS_PER_POINT
            if credits_to_give > 0:
                try:
                    await bank.deposit_credits(member, credits_to_give)
                except Exception as e:
                    print(f"Failed to deposit credits for {member}: {e}")

        results.sort(key=lambda x: x[0])
        
        score_lines = []
        for points, member in results:
            score_lines.append(f"{member.display_name}: **{points}**")
            
        score_str = "\n".join(score_lines)

        try:
            currency = await bank.get_currency_name(channel.guild)
        except:
            currency = "credits"

        yay2 = self._find_emoji("yay2")
        yay = self._find_emoji("yay")
        yay2_str = str(yay2) if yay2 else ":yay2:"
        yay_str = str(yay) if yay else ":yay:"

        embed = discord.Embed(title=f"{yay2_str} Gortle #{game_num} Solved! {yay_str}", 
                              description=f"**{winner.mention}** guessed the word correctly!", 
                              color=discord.Color.gold())
        embed.add_field(name="Solution", value=await self.config.current_word(), inline=False)
        
        if score_str:
            embed.add_field(name="Round Scores", value=score_str, inline=False)
            
        embed.set_footer(text=f"Participants received +2 points and {self.CREDITS_PER_POINT} {currency} per point earned!")
        
        thumb = await self.config.guild(channel.guild).thumbnail_url()
        if thumb:
            embed.set_thumbnail(url=thumb)

        await channel.send(embed=embed)

    async def handle_loss(self, channel, solution):
        await self.config.consecutive_no_guesses.set(0)
        await self.config.game_active.set(False)
        embed = discord.Embed(title="Gortle Failed!", 
                              description=f"Max guesses reached! The word was **{solution.upper()}**.", 
                              color=discord.Color.red())
        
        thumb = await self.config.guild(channel.guild).thumbnail_url()
        if thumb:
            embed.set_thumbnail(url=thumb)
            
        await channel.send(embed=embed)
    
    @commands.group()
    async def gortle(self, ctx):
        """Gortle game commands."""
        pass

    @gortle.command()
    async def addword(self, ctx, word: str):
        """Suggest a word to be added to the dictionary."""
        word = word.lower().strip()
        if len(word) != 6 or not word.isalpha():
            return await ctx.send("Word must be exactly 6 letters.")
        
        if word in self.guesses:
            return await ctx.send("That word is already in the dictionary.")
            
        async with self.config.suggested_words() as suggestions:
            if word in suggestions:
                return await ctx.send("That word has already been suggested.")
            suggestions.append(word)
            
        await ctx.send(f"Suggestion recorded: **{word}**")

    @commands.command(aliases=["gortlehow"])
    async def teachmehowtogortle(self, ctx):
        """Shows the Gortle rules and settings."""
        cooldown_s = await self.config.guild(ctx.guild).cooldown_seconds()
        cooldown_m = cooldown_s / 60
        if cooldown_m.is_integer():
            cooldown_str = str(int(cooldown_m))
        else:
            cooldown_str = f"{cooldown_m:.1f}"

        freq = await self.config.schedule_auto_freq()
        if freq == 0:
            schedule_str = "never (Manual only)"
        else:
            minutes = 60 / freq
            if minutes.is_integer():
                schedule_str = f"{int(minutes)} minutes"
            else:
                schedule_str = f"{minutes:.1f} minutes"

        try:
            currency = await bank.get_currency_name(ctx.guild)
        except:
            currency = "credits"

        credits_rate = self.CREDITS_PER_POINT

        desc = (
            "### How to Play:\n"
            "- Type a six-letter word to make a guess. For example `!friend`\n"
            "- You get points if your guess discovers the right letter in the wrong position (yellow) "
            "or the right letter in the right position (green).\n"
            "- If the timer runs out or you don't guess it in 9 tries, the game ends.\n\n"
            "### Server Settings:\n"
            f"- There is a cooldown of {cooldown_str} minutes between guesses\n"
            f"- A new Gortle is published every {schedule_str}\n\n"
            "### Rewards:\n"
            "- A yellow letter gives you 1 point.\n"
            "- Turning a yellow letter green gives you 1 point.\n"
            "- Finding a green letter gives you 2 points.\n"
            "- If the Gortle is solved, everyone who posted a guess gets 2 points.\n"
            f"- If the Gortle is solved, you also get {credits_rate} {currency} deposited per point earned."
        )

        embed = discord.Embed(
            title="Teach Me How To Gortle",
            description=desc,
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

    @commands.command()
    async def gortletop(self, ctx):
        """Shows the Gortle leaderboard."""
        members = await self.config.all_members(ctx.guild)
        if not members:
            return await ctx.send("No scores yet.")

        sorted_data = sorted(members.items(), key=lambda x: x[1]['score'], reverse=True)
        
        table_data = []
        for i, (uid, data) in enumerate(sorted_data[:10], 1):
            user = ctx.guild.get_member(uid)
            name = user.display_name if user else "Unknown"
            table_data.append([i, name, data['score'], data['weekly_score']])
            
        table = tabulate(table_data, headers=["#", "User", "Total", "Weekly"], tablefmt="simple")
        
        embed = discord.Embed(title="Gortle Leaderboard", description=box(table, lang="c"), color=discord.Color.blue())
        await ctx.send(embed=embed)

    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def gortleset(self, ctx):
        """Configuration for Gortle."""
        pass
    
    @gortleset.command()
    async def suggestions(self, ctx):
        """View suggested words."""
        words = await self.config.suggested_words()
        if not words:
            return await ctx.send("No pending suggestions.")
        await ctx.send(box(", ".join(words)))

    @gortleset.command()
    async def view(self, ctx):
        """View all current Gortle settings."""
        guild_data = await self.config.guild(ctx.guild).all()
        channel_id = guild_data.get('channel_id')
        role_id = guild_data.get('mention_role')
        thumb_url = guild_data.get('thumbnail_url') or "None"
        cooldown = guild_data.get('cooldown_seconds', 60)
        
        channel_obj = ctx.guild.get_channel(channel_id) if channel_id else "Not Set"
        role_obj = ctx.guild.get_role(role_id) if role_id else "Not Set"

        freq = await self.config.schedule_auto_freq()
        manual_max = await self.config.schedule_manual_max()
        prize = await self.config.win_amount()
        
        w_role_id = await self.config.weekly_role_id()
        w_day = await self.config.weekly_role_day()
        w_hour = await self.config.weekly_role_hour()
        
        w_role_obj = ctx.guild.get_role(w_role_id) if w_role_id else "Not Set"
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        w_day_str = days[w_day] if 0 <= w_day <= 6 else w_day

        synced_count = len(self.emoji_cache)
        app_count = len(self.app_emoji_cache)

        table_data = [
            ["Channel", str(channel_obj)],
            ["Mention Role", str(role_obj)],
            ["Thumbnail", thumb_url[:30] + "..." if len(thumb_url) > 30 else thumb_url],
            ["Cooldown", f"{cooldown}s"],
            ["Prize Amt", prize],
            ["Auto Freq", f"{freq}/hr"],
            ["Manual Max", f"{manual_max}/hr"],
            ["Weekly Role", str(w_role_obj)],
            ["Weekly Time", f"{w_day_str} @ {w_hour}:00 UTC"],
            ["Synced Emojis", f"{synced_count} IDs"],
            ["App Emojis", f"{app_count} found"]
        ]
        
        table = tabulate(table_data, headers=["Setting", "Value"], tablefmt="presto")
        await ctx.send(box(table))

    @gortleset.command()
    async def channel(self, ctx, channel: discord.TextChannel):
        """Set the channel for Gortle games."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"Gortle channel set to {channel.mention}")

    @gortleset.command()
    async def role(self, ctx, role: discord.Role):
        """Set the role to verify/mention for new games."""
        await self.config.guild(ctx.guild).mention_role.set(role.id)
        await ctx.send(f"Notification role set to {role.name}")
    
    @gortleset.command()
    async def thumbnail(self, ctx, url: str = None):
        """Set the thumbnail URL for the game embeds. Leave empty to clear."""
        if not url:
            await self.config.guild(ctx.guild).thumbnail_url.set(None)
            await ctx.send("Thumbnail cleared.")
        else:
            if not url.startswith("http"):
                 return await ctx.send("That doesn't look like a valid URL.")
            await self.config.guild(ctx.guild).thumbnail_url.set(url)
            await ctx.send(f"Thumbnail set to: <{url}>")

    @gortleset.command()
    async def schedule(self, ctx, auto_freq: int, manual_max: int):
        """Set the game schedule logic.
        
        auto_freq: How many times per hour to auto-post a game (e.g., 2 = every 30 mins). Set 0 to disable.
        manual_max: How many manual games users can start per hour. Set 0 for unlimited.
        """
        if auto_freq < 0:
            return await ctx.send("Auto frequency cannot be negative.")
        if manual_max < 0:
            return await ctx.send("Manual max cannot be negative.")
            
        await self.config.schedule_auto_freq.set(auto_freq)
        await self.config.schedule_manual_max.set(manual_max)
        
        await self.config.next_game_timestamp.set(0)
        
        msg = f"Schedule updated:\n- Auto-post: {auto_freq} times/hour\n- Manual limit: {manual_max} games/hour"
        await ctx.send(msg)

    @gortleset.command()
    async def cooldown(self, ctx, seconds: int):
        """Set the user guess cooldown in seconds."""
        await self.config.guild(ctx.guild).cooldown_seconds.set(seconds)
        await ctx.send(f"Cooldown set to {seconds} seconds.")

    @gortleset.command()
    async def prize(self, ctx, amount: int):
        """Set the bank credit prize for winning."""
        await self.config.win_amount.set(amount)
        await ctx.send(f"Winner prize set to {amount}.")

    @gortleset.command()
    async def weekly(self, ctx, role: discord.Role, day: int, hour: int):
        """Configure the weekly winner role.
        Day: 0=Monday, 6=Sunday. Hour: 0-23 (UTC)."""
        if not (0 <= day <= 6) or not (0 <= hour <= 23):
            return await ctx.send("Day must be 0-6, Hour must be 0-23.")
        
        await self.config.weekly_role_id.set(role.id)
        await self.config.weekly_role_day.set(day)
        await self.config.weekly_role_hour.set(hour)
        await ctx.send(f"Weekly role {role.name} will be awarded on day {day} at hour {hour} UTC.")

    @gortleset.command()
    async def reloadlists(self, ctx):
        """Reloads the word lists from the file system."""
        self._load_word_lists()
        await ctx.send(f"Lists reloaded. Solutions: {len(self.solutions)}, Guesses: {len(self.guesses)}")

    @gortleset.command()
    async def clearall(self, ctx):
        """Clear all scores."""
        await self.config.clear_all_members(ctx.guild)
        await ctx.send("All scores cleared.")
        
    @gortleset.command()
    async def hardreset(self, ctx):
        """Resets the game number, used words, and all user scores."""
        await ctx.send("Are you sure you want to reset EVERYTHING? This includes the game count, history of used words, and all user leaderboards. Type `yes` to confirm.")
        try:
            pred = lambda m: m.author == ctx.author and m.channel == ctx.channel and m.content.lower() == "yes"
            await self.bot.wait_for("message", check=pred, timeout=30)
        except asyncio.TimeoutError:
            return await ctx.send("Reset cancelled.")
            
        # Perform reset
        await self.config.game_number.set(0)
        await self.config.used_words.set([])
        await self.config.clear_all_members(ctx.guild)
        await self.config.game_active.set(False)
        await ctx.send("Gortle hard reset complete.")

    @gortleset.command()
    async def removeuser(self, ctx, member: discord.Member):
        """Remove a specific user from stats."""
        await self.config.member(member).clear()
        await ctx.send(f"Stats cleared for {member.display_name}.")

    @gortleset.command()
    async def clean(self, ctx):
        """Remove users who are no longer in the server."""
        members = await self.config.all_members(ctx.guild)
        count = 0
        for uid in list(members.keys()):
            if not ctx.guild.get_member(uid):
                await self.config.member_from_ids(ctx.guild.id, uid).clear()
                count += 1
        await ctx.send(f"Removed {count} users no longer in the server.")

    @gortleset.command()
    @checks.is_owner()
    async def syncemojis(self, ctx):
        """
        Scans THIS server for Gortle emojis and saves their IDs globally.
        Run this command inside the server that holds your custom emojis.
        
        Looks for: greena-z, yellowa-z, greya-z, whitea-z, yay, yay2, greysquare.
        """
        valid_prefixes = ["green", "yellow", "grey", "white"]
        found_count = 0
        new_map = {}

        for char in "abcdefghijklmnopqrstuvwxyz":
            for prefix in valid_prefixes:
                target_name = f"{prefix}{char}"
                emoji = discord.utils.get(ctx.guild.emojis, name=target_name)
                if emoji:
                    new_map[target_name] = emoji.id
                    found_count += 1
        
        extras = ["yay", "yay2", "greysquare"]
        for name in extras:
            emoji = discord.utils.get(ctx.guild.emojis, name=name)
            if emoji:
                new_map[name] = emoji.id
                found_count += 1
        
        async with self.config.emoji_map() as m:
            m.update(new_map)
        
        self.emoji_cache.update(new_map)
        
        await ctx.send(f"Synced {found_count} emojis from **{ctx.guild.name}** to the global Gortle database.")
        
    @gortleset.command()
    @checks.is_owner()
    async def refreshappemojis(self, ctx):
        """Manually refreshes the cache of Application Emojis."""
        try:
            emojis = await self.bot.fetch_application_emojis()
            self.app_emoji_cache = emojis
            await ctx.send(f"Refreshed. Found {len(emojis)} Application Emojis.")
        except Exception as e:
            await ctx.send(f"Failed: {e}")