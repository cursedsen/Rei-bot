import discord
import random
import json
import re
import base64
import time
from typing import Union
import asyncio

from utils.helpers import PermissionHandler
from discord.ext import commands
from datetime import datetime, timedelta
from utils.auditlogs import ModLogger


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.mod_log_file = "data/mod_logs.json"
        self.last_case_time = 0
        self.case_counter = 0
        self.logger = ModLogger(bot)

    def generate_case_id(self) -> str:
        """Generate a unique case ID based on timestamp"""
        current_time = int(time.time())
        
        if current_time == self.last_case_time:
            self.case_counter += 1
        else:
            self.last_case_time = current_time
            self.case_counter = 0
        
        unique_num = (current_time << 16) | (self.case_counter << 8) | random.randint(0, 255)
        
        case_id = base64.b32encode(unique_num.to_bytes(8, 'big')).decode('utf-8').rstrip('=')[:8]
        return case_id

    async def save_mod_action(self, guild_id: int, action: dict):
        """Save a moderation action to the records"""
        try:
            with open(self.mod_log_file, 'r') as f:
                records = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            records = {}

        guild_id = str(guild_id)
        if guild_id not in records:
            records[guild_id] = {
                'cases': {},
                'users': {}
            }

        case_id = self.generate_case_id()
        action['case_id'] = case_id
        
        records[guild_id]['cases'][case_id] = action
        
        user_id = str(action['user_id'])
        user = await self.bot.fetch_user(action['user_id'])
        
        if user_id not in records[guild_id]['users']:
            records[guild_id]['users'][user_id] = {
                'username': f"{user.name}#{user.discriminator}" if user.discriminator != '0' else user.name,
                'cases': []
            }
        else:
            records[guild_id]['users'][user_id]['username'] = f"{user.name}#{user.discriminator}" if user.discriminator != '0' else user.name
                
        records[guild_id]['users'][user_id]['cases'].append(case_id)

        with open(self.mod_log_file, 'w') as f:
            json.dump(records, f, indent=2)
            
        return case_id

    @commands.command(aliases=['history', 'infractions'])
    @PermissionHandler.has_permissions(kick_members=True)
    async def records(self, ctx, user: Union[discord.Member, discord.User, str]):
        """View a member's moderation record"""
        try:
            if isinstance(user, str):
                if user.isdigit():
                    user = await self.bot.fetch_user(int(user))
                else:
                    mention_match = re.match(r'<@!?(\d+)>', user)
                    if mention_match:
                        user = await self.bot.fetch_user(int(mention_match.group(1)))
                    else:
                        await ctx.send("Please provide a valid user ID or mention.")
                        return

            with open(self.mod_log_file, 'r') as f:
                records = json.load(f)

            guild_records = records.get(str(ctx.guild.id), {})
            user_data = guild_records.get('users', {}).get(str(user.id))

            if not user_data or not user_data.get('case_ids', []):
                await ctx.send(f"No moderation records found for {user.mention}")
                return

            embed = discord.Embed(
                title=f"Member records | Page 1/1",
                color=0x2B2D31,
                timestamp=datetime.utcnow()
            )
            
            embed.description = f"**{user.name}**\nMention: {user.mention}\n```javascript\nID: {user.id}```\n**Total records:** {len(user_data['case_ids'])}"
            embed.set_thumbnail(url=user.display_avatar.url)

            cases = guild_records.get('cases', {})
            cases_to_show = user_data['case_ids'][-10:]
            cases_to_show.reverse()
            
            for case_id in cases_to_show:
                if case_id not in cases:
                    continue
                    
                record = cases[case_id]
                action_time = datetime.fromisoformat(record['timestamp'])
                moderator = ctx.guild.get_member(record['mod_id'])
                mod_name = moderator.name if moderator else "Unknown moderator"

                embed.add_field(
                    name=f"**{record['action']}**",
                    value=(
                        f"**Case ID:** `{case_id}`\n"
                        f"**Moderator:** {moderator.mention if moderator else mod_name}\n"
                        f"When: {discord.utils.format_dt(action_time)}\n"
                        f"> **Reason:**\n> {record['reason'] or 'No reason provided'}"
                        + (f"\n> **Duration:** {record['duration']}" if 'duration' in record else "")
                    ),
                    inline=False
                )

            embed.set_footer(text=f"Most recent {min(len(user_data['case_ids']), 10)} of {len(user_data['case_ids'])} records")
            await ctx.send(embed=embed)

        except FileNotFoundError:
            await ctx.send("No moderation records exist yet.")
        except json.JSONDecodeError:
            await ctx.send("Error reading moderation records.")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    @commands.command(aliases=['editcase'])
    @PermissionHandler.has_permissions(kick_members=True)
    async def editrecord(self, ctx, case_id: str, *, new_reason: str):
        """Edit the reason for a moderation case"""
        try:
            with open(self.mod_log_file, 'r') as f:
                records = json.load(f)
                
            guild_records = records.get(str(ctx.guild.id), {})
            case = guild_records.get('cases', {}).get(case_id)
            
            if not case:
                await ctx.send(f"Case ID `{case_id}` not found.")
                return
            
            case['reason'] = new_reason
            case['edited_by'] = ctx.author.id
            case['edited_at'] = datetime.utcnow().isoformat()
            
            with open(self.mod_log_file, 'w') as f:
                json.dump(records, f, indent=2)
            
            user = ctx.guild.get_member(case['user_id']) or await ctx.guild.fetch_member(case['user_id'])
            mod = ctx.guild.get_member(case['mod_id'])
            
            embed = discord.Embed(
                title=f"Case updated",
                description=f"**Case:** {case_id}\n**Action:** {case['action']}\n**Target:** {user.mention}\n**New reason:** {new_reason}",
                color=discord.Color.blue(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="Original moderator", value=mod.mention if mod else "Unknown")
            embed.add_field(name="Edited by", value=ctx.author.mention)
            
            await ctx.send("Done 👍")
            
            mod_audit_channel_id = self.bot.settings.get_server_setting(ctx.guild.id, "log_channel_mod_audit")
            if mod_audit_channel_id:
                channel = ctx.guild.get_channel(int(mod_audit_channel_id))
                if channel:
                    await channel.send(embed=embed)
                    
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    #################################
    ## Kick Command
    #################################      
    @commands.command(aliases=['punch'])
    @PermissionHandler.has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason=None):
        """Kick a member from the server"""
        try:
            if member.top_role >= ctx.author.top_role:
                await ctx.send("You cannot kick someone with a higher or equal role!")
                return

            confirm_view = discord.ui.View()
            confirm_button = discord.ui.Button(label="Confirm", style=discord.ButtonStyle.danger)
            cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)

            async def confirm_callback(interaction):
                if interaction.user != ctx.author:
                    await interaction.response.send_message("You cannot interact with this confirmation.", ephemeral=True)
                    return

                await member.kick(reason=reason)
                await self.logger.log_action(ctx, "Kick", member, reason)
                await confirm_message.edit(content=f"**{member.name}** has been kicked.", view=None)
                await interaction.response.defer()

            async def cancel_callback(interaction):
                if interaction.user != ctx.author:
                    await interaction.response.send_message("You cannot interact with this confirmation.", ephemeral=True)
                    return

                await confirm_message.edit(content="Guess not then.", view=None)
                await interaction.response.defer()

            confirm_button.callback = confirm_callback
            cancel_button.callback = cancel_callback
            confirm_view.add_item(confirm_button)
            confirm_view.add_item(cancel_button)

            confirm_message = await ctx.send(f"Are you sure you want to kick **{member.name}**?", view=confirm_view)

        except discord.Forbidden:
            await ctx.send("I don't have permission to kick that user!")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    #################################
    ## Ban Command
    #################################   
    @commands.command(aliases=['kill'])
    @PermissionHandler.has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member, *, reason=None):
        """Ban a member from the server"""
        try:
            if member.top_role >= ctx.author.top_role:
                await ctx.reply("You cannot ban someone with a higher or equal role!")
                return

            confirm_view = discord.ui.View()
            confirm_button = discord.ui.Button(label="Confirm", style=discord.ButtonStyle.danger)
            cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
            compromised_button = discord.ui.Button(label="Compromised Account", style=discord.ButtonStyle.primary)

            async def confirm_callback(interaction):
                if interaction.user != ctx.author:
                    await interaction.response.send_message("This is not for you!", ephemeral=True)
                    return

                await member.ban(reason=reason)
                await self.logger.log_action(ctx, "Ban", member, reason)
                await member.send(f"You were banned from **{ctx.guild.name}**!\nReason: ` {reason or 'No reason provided'} `")
                
                with open('data/strings.json', 'r') as f:
                    strings = json.load(f)
                    action = random.choice(strings['user_was_x'])
                
                await confirm_message.edit(content=f"**{member.name}** was {action}", view=None)
                await interaction.response.defer()

            async def cancel_callback(interaction):
                if interaction.user != ctx.author:
                    await interaction.response.send_message("This is not for you!", ephemeral=True)
                    return

                await confirm_message.edit(content="Guess not then.", view=None)
                await interaction.response.defer()

            async def compromised_callback(interaction):
                if interaction.user != ctx.author:
                    await interaction.response.send_message("This is not for you!", ephemeral=True)
                    return

                try:
                    await interaction.response.defer()
                    
                    dm_message = (
                        f"You were banned from {ctx.guild.name} because your account showed signs of being compromised.\n\n"
                        "For your security, please:\n"
                        "1. Change your Discord password\n"
                        "2. Enable 2-factor authentication\n"
                        "3. Remove any suspicious authorized apps\n"
                        "4. Check for suspicious login locations\n\n"
                        "You have been automatically unbanned and can rejoin once you've secured your account."
                    )
                    try:
                        await member.send(dm_message)
                    except:
                        pass

                    await member.ban(reason="Compromised account", delete_message_days=1)
                    await self.logger.log_action(ctx, "Ban", member, "Compromised account")
                    
                    await asyncio.sleep(2)
                    await ctx.guild.unban(member)
                    
                    await interaction.message.edit(f"**{member.name}** was banned for compromised account.")
                except Exception as e:
                    await interaction.message.edit(f"Error handling compromised account: {str(e)}")

            confirm_button.callback = confirm_callback
            cancel_button.callback = cancel_callback
            compromised_button.callback = compromised_callback
            confirm_view.add_item(confirm_button)
            confirm_view.add_item(cancel_button)
            confirm_view.add_item(compromised_button)

            confirm_message = await ctx.reply(f"Are you sure you want to ban **{member.name}**?", view=confirm_view)


        except discord.Forbidden:
            await ctx.send("I don't have permission to ban that user!")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    #################################
    ## Unban Command
    #################################   
    @commands.command(aliases=['pardon'])
    @PermissionHandler.has_permissions(ban_members=True)
    async def unban(self, ctx, *, user_input):
        """Unban a member from the server"""
        try:
            user_id = None
            if user_input.isdigit():
                user_id = int(user_input)
            else:
                mention_match = re.match(r'<@!?(\d+)>', user_input)
                if mention_match:
                    user_id = int(mention_match.group(1))
            
            if not user_id:
                await ctx.send("Please provide a valid user ID or mention.")
                return

            try:
                ban_entry = await ctx.guild.fetch_ban(discord.Object(id=user_id))
                user = ban_entry.user
            except discord.NotFound:
                await ctx.send("That user is not banned.")
                return

            await ctx.guild.unban(user)
            await self.logger.log_action(ctx, "Unban", user, "No reason provided")
            await ctx.send(f"**{user.name}** has been unbanned.")

        except discord.Forbidden:
            await ctx.send("I don't have permission to unban users!")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    #################################
    ## Timeout Command
    #################################   
    @commands.command(aliases=['timeout', 'silence'])
    @PermissionHandler.has_permissions(moderate_members=True)
    async def mute(self, ctx, member: discord.Member, time: str = None, *, reason=None):
        """Timeout a member (e.g. 1h, 30m, 1d)"""
        try:
            if member.top_role >= ctx.author.top_role:
                await ctx.send("You cannot timeout someone with a higher or equal role!")
                return

            if not time:
                await ctx.send("Please provide a duration (e.g. 1h, 30m, 1d)")
                return

            duration = 0
            if time.endswith('m'): duration = int(time[:-1]) * 60
            elif time.endswith('h'): duration = int(time[:-1]) * 60 * 60
            elif time.endswith('d'): duration = int(time[:-1]) * 24 * 60 * 60
            else:
                await ctx.send("Invalid duration format. Use m (minutes), h (hours), or d (days)")
                return

            if duration <= 0:
                await ctx.send("Duration must be positive")
                return

            await member.timeout(discord.utils.utcnow() + timedelta(seconds=duration), reason=reason)
            await self.logger.log_action(ctx, "Timeout", member, f"{time} - {reason if reason else 'No reason provided'}")
            await ctx.send(f"**{member.name}** has been timed out for **{time}**")

        except discord.Forbidden:
            await ctx.send("I don't have permission to timeout that user!")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    #################################
    ## Unmute Command
    #################################   
    @commands.command(aliases=['unsilence'])
    @PermissionHandler.has_permissions(moderate_members=True)
    async def unmute(self, ctx, member: discord.Member, *, reason=None):
        """Remove timeout from a member"""
        try:
            if member.top_role >= ctx.author.top_role:
                await ctx.send("You cannot unmute someone with a higher or equal role!")
                return

            if not member.is_timed_out():
                mute_role_id = self.bot.settings.get_server_setting(ctx.guild.id, "mute_role")
                if not mute_role_id:
                    await ctx.send("This user is not muted.")
                    return
                
                mute_role = ctx.guild.get_role(int(mute_role_id))
                if not mute_role or mute_role not in member.roles:
                    await ctx.send("This user is not muted.")
                    return
                
                await member.remove_roles(mute_role, reason=reason)
            else:
                await member.timeout(None, reason=reason)

            await self.logger.log_action(ctx, "Unmute", member, reason or "No reason provided")
            await ctx.send(f"**{member.name}** has been unmuted.")

        except discord.Forbidden:
            await ctx.send("I don't have permission to unmute that user!")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    #################################
    ## Role Command (Fuzzy Search)
    #################################
    def find_best_match(self, input_str, roles):
        """Find the best matching role using fuzzy matching"""
        search_term = input_str.lower()
        best_match = None
        highest_similarity = 0

        for role in roles:
            role_name = role.name.lower()

            if role_name in search_term or search_term in role_name:
                similarity = max(
                    len(role_name) / len(search_term),
                    len(search_term) / len(role_name)
                )
                if similarity > highest_similarity:
                    highest_similarity = similarity
                    best_match = role
                continue

            max_length = max(len(role_name), len(search_term))
            distance = 0
            for i in range(min(len(role_name), len(search_term))):
                if role_name[i] != search_term[i]:
                    distance += 1
            distance += abs(len(role_name) - len(search_term))

            similarity = 1 - (distance / max_length)
            if similarity > highest_similarity and similarity > 0.5:
                highest_similarity = similarity
                best_match = role

        return best_match

    #################################
    ## Role Command
    ################################
    @commands.command()
    @PermissionHandler.has_permissions(manage_roles=True)
    async def role(self, ctx, member: discord.Member, *, role_input: str):
        """Add or remove a role from a member"""
        try:
            role = None
            if role_input.startswith('<@&') and role_input.endswith('>'):
                role_id = int(role_input[3:-1])
                role = ctx.guild.get_role(role_id)
            elif role_input.isdigit():
                role = ctx.guild.get_role(int(role_input))
            else:
                role = discord.utils.find(
                    lambda r: r.name.lower() == role_input.lower(), 
                    ctx.guild.roles
                )

                if not role:
                    role = self.find_best_match(role_input, ctx.guild.roles)

            if not role:
                await ctx.send("Could not find that role.")
                return

            if role >= ctx.author.top_role and not ctx.author.guild_permissions.administrator:
                await ctx.send("You cannot manage a role equal to or higher than your highest role.")
                return

            if role in member.roles:
                await member.remove_roles(role)
                await ctx.send(f"Removed role **{role.name}** from **{member.name}**")
            else:
                await member.add_roles(role)
                await ctx.send(f"Added role **{role.name}** to **{member.name}**")

        except discord.Forbidden:
            await ctx.send("I don't have permission to manage roles.")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    #################################
    ## Purge Command
    #################################
    @commands.command()
    @PermissionHandler.has_permissions(manage_messages=True)    
    async def purge(self, ctx, amount: int, *, flags: str = None):
        """Delete messages with advanced filtering
        Flags:
        --user @user: Messages from specific user
        --contains text: Messages containing text
        --startswith text: Messages starting with text
        --endswith text: Messages ending with text
        --links: Messages containing links
        --invites: Messages containing Discord invites
        --images: Messages containing attachments
        --embeds: Messages containing embeds
        --bots: Messages from bots
        --humans: Messages from humans
        --emoji: Messages containing emoji
        --reactions: Messages with reactions
        --pins: Include pinned messages (default: excluded)"""
        
        def message_check(message):
            if message.pinned and ('pins' not in flag_dict):
                return False
                
            if flags is None:
                return True
                
            flag_dict = {}
            current_flag = None
            current_value = []
            
            for part in flags.split():
                if part.startswith('--'):
                    if current_flag:
                        flag_dict[current_flag] = ' '.join(current_value)
                    current_flag = part[2:]
                    current_value = []
                else:
                    current_value.append(part)
                    
            if current_flag:
                flag_dict[current_flag] = ' '.join(current_value)
            
            if 'user' in flag_dict:
                user_id = re.findall(r'\d+', flag_dict['user'])[0]
                if str(message.author.id) != user_id:
                    return False
            
            if 'contains' in flag_dict and flag_dict['contains'].lower() not in message.content.lower():
                return False
            if 'startswith' in flag_dict and not message.content.lower().startswith(flag_dict['startswith'].lower()):
                return False
            if 'endswith' in flag_dict and not message.content.lower().endswith(flag_dict['endswith'].lower()):
                return False
            
            if 'links' in flag_dict and not re.search(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', message.content):
                return False
            if 'invites' in flag_dict and not re.search(r'discord\.gg/\w+', message.content):
                return False
            if 'images' in flag_dict and not message.attachments:
                return False
            if 'embeds' in flag_dict and not message.embeds:
                return False
            if 'bots' in flag_dict and not message.author.bot:
                return False
            if 'humans' in flag_dict and message.author.bot:
                return False
            if 'emoji' in flag_dict and not re.findall(r'<a?:\w+:\d+>|[\U0001F300-\U0001F9FF]', message.content):
                return False
            if 'reactions' in flag_dict and not message.reactions:
                return False
                
            return True

        try:
            deleted = await ctx.channel.purge(limit=amount + 1, check=message_check)
            msg = await ctx.send(f"Deleted {len(deleted) - 1} messages.")
            await msg.delete(delay=3)
            
            mod_audit_channel_id = self.bot.settings.get_server_setting(ctx.guild.id, "log_channel_mod_audit")
            if mod_audit_channel_id:
                channel = ctx.guild.get_channel(int(mod_audit_channel_id))
                if channel:
                    embed = discord.Embed(
                        title="Bulk messages deleted",
                        description=f"**Channel:** {ctx.channel.mention}\n**Amount:** {len(deleted) - 1}\n**Moderator:** {ctx.author.mention}",
                        color=discord.Color.red(),
                        timestamp=datetime.utcnow()
                    )
                    if flags:
                        embed.add_field(name="Filters used", value=f"```{flags}```")
                    await channel.send(embed=embed)

        except discord.Forbidden:
            await ctx.send("I don't have permission to delete messages.")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    #################################
    ## Add Note Command
    #################################
    @commands.command()
    @PermissionHandler.has_permissions(kick_members=True)
    async def addnote(self, ctx, user: Union[discord.Member, discord.User, str], *, note: str):
        """Add a note to a user's record"""
        try:
            if isinstance(user, str):
                if user.isdigit():
                    user = await self.bot.fetch_user(int(user))
                else:
                    mention_match = re.match(r'<@!?(\d+)>', user)
                    if mention_match:
                        user = await self.bot.fetch_user(int(mention_match.group(1)))
                    else:
                        await ctx.send("Please provide a valid user ID or mention.")
                        return
                    
            await self.logger.log_action(ctx, "Note", user, note)
            await ctx.send(f"Added note to **{user.name}**'s record.")

        except discord.NotFound:
            await ctx.send("Could not find that user.")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    #################################
    ## Lock Command
    #################################
    @commands.command()
    @PermissionHandler.has_permissions(manage_channels=True)
    async def lock(self, ctx, channel: discord.TextChannel = None):
        """Lock a channel"""
        try:
            channel = channel or ctx.channel
            overwrites = channel.overwrites_for(ctx.guild.default_role)
            
            if not overwrites.send_messages is False:
                overwrites.send_messages = False
                await channel.set_permissions(ctx.guild.default_role, overwrite=overwrites)

                await ctx.send(f"{channel.mention} has been locked.")
            else:
                await ctx.send(f"{channel.mention} is already locked.")

        except discord.Forbidden:
            await ctx.send("I don't have permission to manage channel permissions!")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    #################################
    ## Unlock Command
    #################################
    @commands.command()
    @PermissionHandler.has_permissions(manage_channels=True)
    async def unlock(self, ctx, channel: discord.TextChannel = None):
        """Unlock a channel"""
        try:
            channel = channel or ctx.channel
            overwrites = channel.overwrites_for(ctx.guild.default_role)
            
            if overwrites.send_messages is False:
                overwrites.send_messages = None
                await channel.set_permissions(ctx.guild.default_role, overwrite=overwrites)
                
                await ctx.send(f"{channel.mention} has been unlocked.")
            else:
                await ctx.send(f"{channel.mention} is not locked.")

        except discord.Forbidden:
            await ctx.send("I don't have permission to manage channel permissions!")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    #################################
    ## Warn Command
    #################################   
    @commands.command()
    @PermissionHandler.has_permissions(kick_members=True)
    async def warn(self, ctx, member: discord.Member, *, reason=None):
        """Warn a member"""
        try:
            if member.top_role >= ctx.author.top_role:
                await ctx.reply("You cannot warn someone with a higher or equal role!")
                return
            
            await self.logger.log_action(ctx, "Warning", member, reason)
            await ctx.reply(f"Warned **{member.name}**" + (f" for: **{reason}**" if reason else ""))

        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

async def setup(bot):
    await bot.add_cog(Moderation(bot))