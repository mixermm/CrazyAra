"""
@file: input_representation
Created on 26.09.19
@project: CrazyAra
@author: queensgambit

Input representation for all available lichess chess variants board states (including  crazyhouse)
which is passed to the neural network
"""

from chess.variant import CrazyhouseBoard
from DeepCrazyhouse.src.domain.variants.constants import (
    BOARD_HEIGHT,
    BOARD_WIDTH,
    CHANNEL_MAPPING_CONST,
    CHANNEL_MAPPING_POS,
    CHANNEL_MAPPING_VARIANTS,
    MAX_NB_MOVES,
    MAX_NB_NO_PROGRESS,
    MAX_NB_PRISONERS,
    NB_CHANNELS_CONST,
    NB_CHANNELS_CONST_CZ,
    NB_CHANNELS_POS,
    NB_CHANNELS_VARIANTS,
    PIECES,
    chess,
    VARIANT_MAPPING_BOARDS)
from DeepCrazyhouse.src.domain.util import MATRIX_NORMALIZER, get_board_position_index, get_row_col, np


def _fill_position_planes(planes_pos, board, board_occ=0):

    # Fill in the piece positions

    # Iterate over both color starting with WHITE
    for idx, color in enumerate(chess.COLORS):
        # the PIECE_TYPE is an integer list in python-chess
        for piece_type in chess.PIECE_TYPES:
            # define the channel by the piece_type (the input representation uses the same ordering as python-chess)
            # we add an offset for the black pieces
            # note that we subtract 1 because in python chess the PAWN has index 1 and not 0
            channel = (piece_type - 1) + idx * len(chess.PIECE_TYPES)
            # iterate over the piece mask and receive every position square of it
            for pos in board.pieces(piece_type, color):
                row, col = get_row_col(pos)
                # set the bit at the right position
                planes_pos[channel, row, col] = 1

    # (II) Fill in the Repetition Data
    # a game to test out if everything is working correctly is: https://lichess.org/jkItXBWy#73
    channel = CHANNEL_MAPPING_POS["repetitions"]

    # set how often the position has already occurred in the game (default 0 times)
    # this is used to check for claiming the 3 fold repetition rule
    if board_occ >= 1:
        planes_pos[channel, :, :] = 1
        if board_occ >= 2:
            planes_pos[channel + 1, :, :] = 1

    # Fill in the Prisoners / Pocket Pieces

    # iterate over all pieces except the king
    for p_type in chess.PIECE_TYPES[:-1]:
        # p_type -1 because p_type starts with 1
        channel = CHANNEL_MAPPING_POS["prisoners"] + p_type - 1

        planes_pos[channel, :, :] = board.pockets[chess.WHITE].count(p_type)
        # the prison for black begins 5 channels later
        planes_pos[channel + 5, :, :] = board.pockets[chess.BLACK].count(p_type)

    # (III) Fill in the promoted pieces
    # iterate over all promoted pieces according to the mask and set the according bit
    channel = CHANNEL_MAPPING_POS["promo"]
    for pos in chess.SquareSet(board.promoted):
        row, col = get_row_col(pos)

        if board.piece_at(pos).color == chess.WHITE:
            planes_pos[channel, row, col] = 1
        else:
            planes_pos[channel + 1, row, col] = 1

    # (III.2) En Passant Square
    # mark the square where an en-passant capture is possible
    channel = CHANNEL_MAPPING_POS["ep_square"]
    if board.ep_square is not None:
        row, col = get_row_col(board.ep_square)
        planes_pos[channel, row, col] = 1

    return planes_pos

def _fill_constant_planes(planes_const, board, board_turn):

    # (IV) Constant Value Inputs
    # (IV.1) Color
    if board_turn == chess.WHITE:
        planes_const[CHANNEL_MAPPING_CONST["color"], :, :] = 1
    # otherwise the mat will remain zero

    # (IV.2) Total Move Count
    planes_const[CHANNEL_MAPPING_CONST["total_mv_cnt"], :, :] = board.fullmove_number
    # alternatively, you could use the half-moves-counter: len(board.move_stack)

    # (IV.3) Castling Rights
    channel = CHANNEL_MAPPING_CONST["castling"]

    # WHITE
    # check for King Side Castling
    if bool(board.castling_rights & chess.BB_H1) is True:
        # White can castle with the h1 rook
        planes_const[channel, :, :] = 1
    # check for Queen Side Castling
    if bool(board.castling_rights & chess.BB_A1) is True:
        planes_const[channel + 1, :, :] = 1

    # BLACK
    # check for King Side Castling
    if bool(board.castling_rights & chess.BB_H8) is True:
        # White can castle with the h1 rook
        planes_const[channel + 2, :, :] = 1
    # check for Queen Side Castling
    if bool(board.castling_rights & chess.BB_A8) is True:
        planes_const[channel + 3, :, :] = 1

    # (IV.4) No Progress Count
    # define a no 'progress' counter
    # it gets incremented by 1 each move
    # however, whenever a piece gets dropped, a piece is captured or a pawn is moved, it is reset to 0
    # halfmove_clock is an official metric in fen notation
    #  -> see: https://en.wikipedia.org/wiki/Forsyth%E2%80%93Edwards_Notation
    no_progress_cnt = board.halfmove_clock

    # check how often the position has already occurred in the game
    planes_const[CHANNEL_MAPPING_CONST["no_progress_cnt"], :, :] = no_progress_cnt

    # set the remaining checks (only needed for "3check")
    if board.uci_variant == "3check":
        channel = CHANNEL_MAPPING_CONST["remaining_checks"]
        if board.remaining_checks[chess.WHITE] <= 2:
            planes_const[channel, :, :] = 1
            if board.remaining_checks[chess.WHITE] == 1:
                planes_const[channel+1, :, :] = 1
        if board.remaining_checks[chess.BLACK] <= 2:
            planes_const[channel+2, :, :] = 1
            if board.remaining_checks[chess.BLACK] == 1:
                planes_const[channel+3, :, :] = 1

    return planes_const


def _fill_variants_plane(board, planes_variants):
    # (V) Variants specification

    # set the is960 boolean flag when active
    if board.chess960 is True:
        planes_variants[CHANNEL_MAPPING_VARIANTS["is960"], :, :] = 1

    # set the current active variant as a one-hot encoded entry
    for variant_name in CHANNEL_MAPPING_VARIANTS:
        if variant_name == board.uci_variant:
            planes_variants[CHANNEL_MAPPING_VARIANTS[variant_name], :, :] = 1
            break


def board_to_planes(board, board_occ=0, normalize=True, crazyhouse_only=False):
    """
    Gets the plane representation of a given board state.
    (No history of past board positions is used.)

    ## Chess Variants:

    Feature | Planes

    --- | ---

    P1 piece | 6 (pieces are ordered: PAWN, KNIGHT, BISHOP, ROOK, QUEEN, KING)

    P2 piece | 6 (pieces are ordered: PAWN, KNIGHT, BISHOP, ROOK, QUEEN, KING)

    Repetitions | 2 (two planes (full zeros/ones) indicating how often the board positions has occurred)

    P1 prisoner count | 5 (pieces are ordered: PAWN, KNIGHT, BISHOP, ROOK, QUEEN) (excluding the KING)

    P2 prisoner count | 5 (pieces are ordered: PAWN, KNIGHT, BISHOP, ROOK, QUEEN) (excluding the KING)

    P1 Promoted Pawns Mask | 1 (binary map indicating the pieces which have been promoted)

    P2 Promoted Pawns Mask | 1 (binary map indicating the pieces which have been promoted)

    En-passant square | 1 (Binary map indicating the square where en-passant capture is possible)

    ---
    27 planes

    * * *

    Colour | 1 (all zeros for black and all ones for white)

    Total move count | 1 (integer value setting the move count (uci notation))

    P1 castling | 2 (One if castling is possible, else zero)

    P2 castling | 2 (One if castling is possible, else zero)

    No-progress count | 1 (Setting the no progress counter as integer values, (described by uci halfmoves format)

    P1 remaining-checks | 2 (only needed for the 3check variant, after 3 checks by one player the game ends)

    P2 remaining-checks | 2 (only needed for the 3check variant, after 3 checks by one player the game ends)

    11 planes

    * * *

    is960 = | 1 (boolean, 1 when active)

    Variants indicator each variant gets a whole channel assigned. All variants are one-hot encoded

    1 - "chess" | 1
    2 - "crazyhouse" | 1
    3 - "kingofthehill" | 1
    4 - "3check" | 1
    5 - "giveaway" | 1
    6 - "atomic" | 1
    7 - "horde" | 1
    8 - "racingkings" | 1

    9 planes

    # --------------

    The history list of the past 7 board states have been removed
    The total number of planes is calculated as follows:

    27 + 11 + 9
    Total: 47 planes

    :param board: Board handle (Python-chess object)
    :param board_occ: Sets how often the board state has occurred before (by default 0)
    :param normalize: True if the inputs shall be normalized to the range [0.-1.]
    :param crazyhouse_only: Boolean indicates if the older crazyhouse only specification shall be used
                            Visit variants.crazyhouse.input_representation for documentation
    :return: planes - the plane representation of the current board state
    """

    # TODO: Remove board.mirror() for black by addressing the according color channel

    # (I) Define the Input Representation for one position
    planes_pos = np.zeros((NB_CHANNELS_POS, BOARD_HEIGHT, BOARD_WIDTH))
    if crazyhouse_only is True:
        # crazyhouse only doesn't contain remaining checks
        planes_const = np.zeros((NB_CHANNELS_CONST_CZ, BOARD_HEIGHT, BOARD_WIDTH))
    else:
        planes_const = np.zeros((NB_CHANNELS_CONST, BOARD_HEIGHT, BOARD_WIDTH))
    if crazyhouse_only is False:
        planes_variants = np.zeros((NB_CHANNELS_VARIANTS, BOARD_HEIGHT, BOARD_WIDTH))
    else:
        planes_variants = False

    # save whose turn it is
    board_turn = chess.WHITE

    # check who's player turn it is and flip the board if it's black turn
    if board.turn == chess.BLACK:
        board_turn = chess.BLACK
        board = board.mirror()

    _fill_position_planes(planes_pos, board, board_occ)
    _fill_constant_planes(planes_const, board, board_turn)
    if crazyhouse_only is False:
        _fill_variants_plane(board, planes_variants)

    # (VI) Merge the Matrix-Stack
    if crazyhouse_only is True:
        planes = np.concatenate((planes_pos, planes_const), axis=0)
    else:
        planes = np.concatenate((planes_pos, planes_const, planes_variants), axis=0)

    # revert the board if the players turn was black
    # ! DO NOT DELETE OR UNCOMMENT THIS BLOCK BECAUSE THE PARAMETER board IS CHANGED IN PLACE !
    if board_turn == chess.BLACK:
        board = board.mirror()

    if normalize is True:
        planes *= MATRIX_NORMALIZER
        # planes = normalize_input_planes(planes)

    # return the plane representation of the given board
    return planes


def planes_to_board(planes, normalized_input=False, crazyhouse_only=False):
    """
    Converts a board in plane representation to the python chess board representation
    see get_planes_of_board() for input encoding description

    :param planes: Input plane representation
    :param normalized_input: True if the input has been normalized to range[0., 1.]
    :param crazyhouse_only: Boolean indicates if the older crazyhouse only specification shall be used
                            Visit variants.crazyhouse.input_representation for documentation
    :return: python chess board object
    """
    # extract the maps for the board position
    planes_pos = planes[:NB_CHANNELS_POS]
    # extract the last maps which for the constant values
    planes_const = planes[NB_CHANNELS_POS:NB_CHANNELS_POS+NB_CHANNELS_CONST]

    if crazyhouse_only is False:
        # extract the variants definition section
        planes_variants = planes[-NB_CHANNELS_VARIANTS:]

        # setup new initial board
        is960 = planes_variants[CHANNEL_MAPPING_VARIANTS["is960"], 0, 0] == 1

        # iterate through all available variants
        board = None
        for variant in CHANNEL_MAPPING_VARIANTS:
            if planes_variants[CHANNEL_MAPPING_VARIANTS[variant]] == 1:
                board = VARIANT_MAPPING_BOARDS[variant](chess960=is960)
                break

        if board is None:
            raise Exception("No chess variant was recognized in your given input planes")
    else:
        board = CrazyhouseBoard()

    # iterate over all piece types
    for idx, piece in enumerate(PIECES):
        # iterate over all fields and set the current piece type
        for row in range(BOARD_HEIGHT):
            for col in range(BOARD_WIDTH):
                # check if there's a piece at the current position
                if planes_pos[idx, row, col] == 1:
                    # check if the piece was promoted
                    promoted = False
                    channel = CHANNEL_MAPPING_POS["promo"]
                    if planes_pos[channel, row, col] == 1 or planes_pos[channel + 1, row, col] == 1:
                        promoted = True

                    board.set_piece_at(
                        square=get_board_position_index(row, col),
                        piece=chess.Piece.from_symbol(piece),
                        promoted=promoted,
                    )

    # (I) Fill in the Repetition Data
    # check how often the position has already occurred in the game
    # TODO: Find a way to set this on the board state
    # -> apparently this isn't possible because it's also not available in the board uci representation

    # ch = channel_mapping['repetitions']

    # Fill in the Prisoners / Pocket Pieces

    # iterate over all pieces except the king
    for p_type in chess.PIECE_TYPES[:-1]:
        # p_type -1 because p_type starts with 1
        channel = CHANNEL_MAPPING_POS["prisoners"] + p_type - 1

        # the full board is filled with the same value
        # it's sufficient to take only the first value
        nb_prisoners = planes_pos[channel, 0, 0]

        # add prisoners for the current player
        # the whole board is set with the same entry, we can just take the first one
        if normalized_input is True:
            nb_prisoners *= MAX_NB_PRISONERS
            nb_prisoners = int(round(nb_prisoners))

        for _ in range(nb_prisoners):
            board.pockets[chess.WHITE].add(p_type)

        # add prisoners for the opponent
        nb_prisoners = planes_pos[channel + 5, 0, 0]
        if normalized_input is True:
            nb_prisoners *= MAX_NB_PRISONERS
            nb_prisoners = int(round(nb_prisoners))

        for _ in range(nb_prisoners):
            board.pockets[chess.BLACK].add(p_type)

    # (I.5) En Passant Square
    # mark the square where an en-passant capture is possible
    channel = CHANNEL_MAPPING_POS["ep_square"]
    ep_square = np.argmax(planes_pos[channel])
    if ep_square != 0:
        # if no entry 'one' exists, index 0 will be returned
        board.ep_square = ep_square

    # (II) Constant Value Inputs
    # (II.1) Total Move Count
    channel = CHANNEL_MAPPING_CONST["total_mv_cnt"]
    total_mv_cnt = planes_const[channel, 0, 0]

    if normalized_input is True:
        total_mv_cnt *= MAX_NB_MOVES
        total_mv_cnt = int(round(total_mv_cnt))

    board.fullmove_number = total_mv_cnt

    # (II.2) Castling Rights
    channel = CHANNEL_MAPPING_CONST["castling"]

    # reset the castling_rights for initialization
    # set to 0, previously called chess.BB_VOID for chess version of 0.23.X and chess.BB_EMPTY for versions > 0.27.X
    board.castling_rights = 0

    # WHITE
    # check for King Side Castling
    # White can castle with the h1 rook

    # add castling option by applying logical-OR operation
    if planes_const[channel, 0, 0] == 1:
        board.castling_rights |= chess.BB_H1
    # check for Queen Side Castling
    if planes_const[channel + 1, 0, 0] == 1:
        board.castling_rights |= chess.BB_A1

    # BLACK
    # check for King Side Castling
    if planes_const[channel + 2, 0, 0] == 1:
        board.castling_rights |= chess.BB_H8
    # check for Queen Side Castling
    if planes_const[channel + 3, 0, 0] == 1:
        board.castling_rights |= chess.BB_A8

    # (II.3) No Progress Count
    channel = CHANNEL_MAPPING_CONST["no_progress_cnt"]
    no_progress_cnt = planes_const[channel, 0, 0]
    if normalized_input is True:
        no_progress_cnt *= MAX_NB_NO_PROGRESS
        no_progress_cnt = int(round(no_progress_cnt))

    board.halfmove_clock = no_progress_cnt

    # set the number of remaining checks (only needed for 3check) and might be mirrored later
    if crazyhouse_only is False:
        channel = CHANNEL_MAPPING_CONST["remaining_checks"]
        if planes_const[channel, 0, 0] == 1:
            board.remaining_checks[chess.WHITE] -= 1
        if planes_const[channel+1, 0, 0] == 1:
            board.remaining_checks[chess.WHITE] -= 1
        if planes_const[channel+2, 0, 0] == 1:
            board.remaining_checks[chess.BLACK] -= 1
        if planes_const[channel+3, 0, 0] == 1:
            board.remaining_checks[chess.BLACK] -= 1

    # (II.4) Color
    channel = CHANNEL_MAPPING_CONST["color"]

    if planes_const[channel, 0, 0] == 1:
        board.board_turn = chess.WHITE
    else:
        board = board.mirror()
        board.board_turn = chess.BLACK

    return board