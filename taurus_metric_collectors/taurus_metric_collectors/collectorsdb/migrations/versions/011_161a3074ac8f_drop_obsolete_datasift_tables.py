"""drop obsolete datasift tables

Revision ID: 161a3074ac8f
Revises: 52c6989ed12e
Create Date: 2015-02-17 13:28:43.428063

"""

# revision identifiers, used by Alembic.
revision = '161a3074ac8f'
down_revision = '52c6989ed12e'

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

def upgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('datasift_agg_stats')

    op.drop_table('datasift_tweet_samples')
    op.drop_table('datasift_tweets')

    op.drop_table('datasift_wordpress_samples')
    op.drop_table('datasift_wordpress_msgs')

    op.drop_table('datasift_tumblr_samples')
    op.drop_table('datasift_tumblr_msgs')
    ### end Alembic commands ###


def downgrade():
    raise NotImplementedError("Rollback is not supported.")

