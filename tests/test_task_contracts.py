import unittest

from app.task_contracts import (
    guard_premature_media_completion_claim,
    is_high_confidence_image_action_request,
    is_high_confidence_media_action_request,
)


class TaskContractTests(unittest.TestCase):
    def test_explicit_image_predicate_accepts_direct_requests(self):
        for request in (
            "Generate an image of a blue mug.",
            "Please send me a photo of the garden.",
            "Candy, please send me an image of a red apple.",
            "Hey Candy, please create a photo of the lake.",
            "Could you create a portrait of my dog?",
            "I would like a picture of a lighthouse.",
            "Can I get an illustration of this idea?",
            "Try another selfie.",
            "Draw a moonlit cabin.",
            "Would you paint a sunset for me?",
            "I'd like you to illustrate a dragon.",
            "Please sketch the view from a window.",
            "Show me an image of a moonlit garden.",
            "Show an image of an empty greenhouse; do not include the persona.",
            "I prefer moonlit gardens. Show me an image of one.",
            "Show me a moonlit garden.",
            "Show an empty greenhouse; do not include the persona.",
            "I prefer moonlit gardens. Show me one.",
            "Show me your outfit.",
            "Candy, please show me your convention outfit.",
            "Please generate an image from this prompt: a blue cup.",
            "Create an image with a caption that says hello.",
            "Create a photo based on this description: a quiet lake.",
            "Using this prompt, please generate an image of a blue cup.",
            "Based on this description, create a photo of a quiet lake.",
            "Add this caption, then create an image of a cat.",
            "Please use this prompt to generate an image of a lighthouse.",
            "Based on this prompt create an image of a dog.",
            "Using this prompt generate an image of a lake.",
            "Can you show me a moonlit garden?",
            "Could you show me your outfit?",
            "Would you show me the lake?",
            "Show me a small cat wearing a hat.",
            "Show me the old car parked by a lake.",
            "Using this prompt, could you generate an image of a lake?",
            "Based on this description would you create a photo of a forest?",
        ):
            with self.subTest(request=request):
                self.assertTrue(is_high_confidence_image_action_request(request))
                self.assertTrue(is_high_confidence_media_action_request(request))

    def test_explicit_image_predicate_rejects_ordinary_non_media_actions(self):
        for request in (
            "Send me your thoughts about that.",
            "Show me how this works.",
            "Create a list of groceries.",
            "Take a moment before answering.",
            "Render a page in the browser.",
            "Please make this explanation clearer.",
            "Candy, send me your thoughts about this image.",
            "Candy, show me how image generation works.",
            "Hey Candy, take a moment to inspect the photo.",
            "Show me my saved memories.",
            "Show me what you mean.",
            "Show me a list of the remaining bugs.",
            "Show me the settings that control images.",
            "Draw a conclusion from these results.",
            "Sketch out a plan for the release.",
            "Illustrate your point with an example.",
            "Create a caption for this image.",
            "Give me feedback on this photo.",
            "Make this image prompt clearer.",
            "Create a grocery list. A picture would help me remember it.",
            "Show me the weather.",
            "Show me a summary of our conversation.",
            "Show me the current settings.",
            "Show me a table of the results.",
            "Show me a better explanation.",
            "Show me the difference between these options.",
            "Show me the error message.",
            "Show me a joke.",
            "Show me the next task.",
            "Show me a calendar.",
            "Show me the dashboard.",
            "Show me this conversation.",
            "Show me a report.",
            "Show me the media catalog.",
            "Show me a chat profile.",
            "Show me a recipe for apple pie.",
            "Show me the cat command in the terminal.",
            "Show me a report about the forest.",
            "Show me an article about the garden.",
            "Show me the address of the lake house.",
            "Show me a story about a dragon.",
            "Show me documentation for the car service.",
            "Show me the dashboard for the car.",
            "Show me a calendar for the garden.",
            "Show me the chat about the cat.",
            "Using this prompt, generate an image caption.",
            "Please use this prompt to create an image description.",
            "Based on this description, create a prompt for an image.",
            "Add this caption, then create an image prompt.",
            "Using this prompt, generate a description for the image.",
            "Using this prompt, create image metadata.",
            "Using this prompt, create image settings.",
            "Using this prompt, create image instructions.",
            "Using this prompt, create image details.",
            "Using this prompt, create image alt text.",
            "Using this prompt, generate image feedback.",
            "Using this prompt, generate image analysis.",
        ):
            with self.subTest(request=request):
                self.assertFalse(is_high_confidence_image_action_request(request))
                self.assertFalse(is_high_confidence_media_action_request(request))

    def test_explicit_image_predicate_rejects_story_discussion_hypothetical_and_quoted_contexts(self):
        for request in (
            "Tell me a story where Candy draws a cat.",
            "Discuss whether you can generate an image.",
            "What if you painted a sunset?",
            "If I asked you to draw a cat, what would happen?",
            "Imagine that someone asked you to send a picture.",
            '"Draw me a picture of a cat."',
            "Quote: generate an image of a lighthouse.",
            'She said, "send me a photo of the garden."',
            "Can you explain how to draw a cat?",
        ):
            with self.subTest(request=request):
                self.assertFalse(is_high_confidence_image_action_request(request))
                self.assertFalse(is_high_confidence_media_action_request(request))

    def test_explicit_video_requests_remain_media_actions_but_not_image_actions(self):
        for request in (
            "Create a short video of a garden.",
            "Could you record a video for me?",
            "I would like an animation of a paper airplane.",
            "Show me your outfit in a short video.",
        ):
            with self.subTest(request=request):
                self.assertFalse(is_high_confidence_image_action_request(request))
                self.assertTrue(is_high_confidence_media_action_request(request))

    def test_all_enabled_image_replies_use_one_platform_owned_acknowledgement(self):
        request = "Please generate and send me a picture of a blue mug."
        for reply in (
            "[Image sent] A simple picture is created. I hope you like it!",
            "Image sent. I hope you like it!",
            "Here is your picture. I have verified the identity match.",
            "Here is that picture for you: [Image]",
            "*holds up my phone and taps the shutter* Ta-da!",
            "Voilà — I think you’re going to love this one.",
            "I'll make it now.",
            "That sounds fun.",
        ):
            with self.subTest(reply=reply):
                guarded, changed = guard_premature_media_completion_claim(request, reply)
                self.assertTrue(changed)
                self.assertEqual(guarded, "I’ll see what I can make for you.")

    def test_platform_acknowledgement_is_idempotent(self):
        guarded, changed = guard_premature_media_completion_claim(
            "Create a picture of a garden.",
            "I’ll see what I can make for you.",
        )

        self.assertFalse(changed)
        self.assertEqual(guarded, "I’ll see what I can make for you.")

    def test_non_image_request_does_not_trigger_image_acknowledgement(self):
        for request in (
            "Send me your thoughts about this.",
            "Tell me a story where you send me a picture.",
            "What if you generated an image?",
        ):
            with self.subTest(request=request):
                guarded, changed = guard_premature_media_completion_claim(
                    request,
                    "Here is your picture. Ta-da!",
                )
                self.assertFalse(changed)
                self.assertEqual(guarded, "Here is your picture. Ta-da!")

    def test_disabled_persona_image_sends_replace_promises_and_completion_claims(self):
        for reply in (
            "I can create that for you.",
            "I'll make that picture now.",
            "Here is that picture for you: [Image]",
            "*taps the shutter* Ta-da!",
            "That sounds fun.",
        ):
            with self.subTest(reply=reply):
                guarded, changed = guard_premature_media_completion_claim(
                    "Create a picture of a garden.",
                    reply,
                    image_sends_allowed=False,
                )

                self.assertTrue(changed)
                self.assertEqual(guarded, "Picture sending is turned off for this persona.")

        video_reply, changed = guard_premature_media_completion_claim(
            "Create a short video of a garden.",
            "I'll create that video now.",
            image_sends_allowed=False,
        )
        self.assertFalse(changed)
        self.assertEqual(video_reply, "I'll create that video now.")

        guarded, changed = guard_premature_media_completion_claim(
            "Create an animated portrait image.",
            "I'll make that animated image now.",
            image_sends_allowed=False,
        )
        self.assertTrue(changed)
        self.assertEqual(guarded, "Picture sending is turned off for this persona.")

    def test_video_only_reply_is_unchanged_when_image_sends_are_enabled(self):
        guarded, changed = guard_premature_media_completion_claim(
            "Create a short video of a garden.",
            "I'll create that video now.",
        )

        self.assertFalse(changed)
        self.assertEqual(guarded, "I'll create that video now.")


if __name__ == "__main__":
    unittest.main()
