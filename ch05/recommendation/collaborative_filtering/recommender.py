from enum import Enum
from typing import Dict, List
import sys
from neo4j import Transaction

from util.fixed_heapq import FixedHeap
from util.sparse_vector import cosine_similarity
from util.graphdb_base import GraphDBBase


class BaseRecommender(GraphDBBase):
    label = None
    property = None
    sparse_vector_query = None
    score_query = None

    def __init__(self, argv):
        super().__init__(command=__file__, argv=argv)

    def compute_and_store_KNN(self, size: int) -> None:
        print("fetching vectors")
        vectors = self.get_vectors()
        # print(vectors["824"])
        print(f"computing KNN for {len(vectors)} vectors")
        for i, (key, vector) in enumerate(vectors.items()):
            # index only vectors
            vector = sorted(vector.keys())
            # if key == "824": print(vector)
            knn = FixedHeap(20)
            for (other_key, other_vector) in vectors.items():
                if key != other_key:
                    # index only vectors
                    other_vector = sorted(other_vector.keys())
                    score = cosine_similarity(vector, other_vector)
                    # if key == "824": 
                    #     print(other_vector)
                    #     print(score)
                    if score > 0:
                        knn.push(score, {"secondNode": other_key, "similarity": score})
                        # if key == "824": print( {"secondNode": other_key, "similarity": score})
            # if key == "824": print(knn.items())
            self.store_KNN(key, knn.items())
            if (i % 1000 == 0) and i > 0:
                print(f"{i} vectors processed...")
        print("KNN computation done")

    def get_vectors(self) -> Dict:
        with self._driver.session() as session:
            tx = session.begin_transaction()
            ids = self.get_elements(tx)
            vectors = {id_: self.get_sparse_vector(tx, id_) for id_ in ids}
        return vectors

    def get_elements(self, tx) -> List[str]:
        print(self.label)
        print(self.property)
        query = f"MATCH (u:{self.label}) RETURN u.{self.property} as id"
        result = tx.run(query).value()
        # print(result)
        return result

    def get_sparse_vector(self, tx: Transaction, current_id: str) -> Dict[int, float]:
        # if current_id == '824':
        #     print("cur id 824")
        #     print(self.sparse_vector_query)
        params = {"id": current_id}
        result = tx.run(self.sparse_vector_query, params)
        return dict(result.values())

    def store_KNN(self, key: str, sims: List[Dict]) -> None:
        deleteQuery = f"""
            MATCH (n:{self.label})-[s:SIMILARITY]->()
            WHERE n.{self.property} = $id
            DELETE s"""

        query = f"""
            MATCH (n:{self.label}) 
            WHERE n.{self.property} = $id 
            UNWIND $sims as sim
            MATCH (o:{self.label}) 
            WHERE o.{self.property} = sim.secondNode 
            CREATE (n)-[s:SIMILARITY {{ value: toFloat(sim.similarity) }}]->(o)"""

        with self._driver.session() as session:
            tx = session.begin_transaction()
            params = {
                "id": key,
                "sims": sims}
            if key == '866': 
                print(sims)
                print(deleteQuery)
                print(query)
            tx.run(deleteQuery, params)
            tx.run(query, params)
            tx.commit()

    def get_recommendations(self, user_id: str, size: int) -> List[int]:
        not_seen_yet_items = self.get_not_seen_yet_items(user_id)
        # if user_id == '866': print(not_seen_yet_items)
        recommendations = FixedHeap(size)
        for item in not_seen_yet_items:
            score = self.get_score(user_id, item)
            recommendations.push(score, item)
            # print(score, item)
        # print(recommendations.items())
        return recommendations.items()

    def get_not_seen_yet_items(self, user_id: str) -> List[int]:
        query = """
                MATCH (user:User {userId:$userId})
                WITH user
                MATCH (item:Item)
                WHERE NOT EXISTS((user)-[:BOOKMARKS]->(item))
                return item.itemId
        """
        with self._driver.session() as session:
            tx = session.begin_transaction()
            params = {"userId": user_id}
            result = tx.run(query, params).value()
        return result

    def get_score(self, user_id: str, item_id: str) -> float:
        with self._driver.session() as session:
            tx = session.begin_transaction()
            params = {"userId": user_id, "itemId": item_id}
            result = tx.run(self.score_query, params)
            result = result.value() + [0.0]
            # if user_id == '866' : print(result[0])
        return result[0]


class UserRecommender(BaseRecommender):
    label = "User"
    property = "userId"
    sparse_vector_query = """
        MATCH (u:User {userId: $id})-[:BOOKMARKS]->(i:Item)
        return id(i) as index, 1.0 as value
        order by index
    """
    score_query = """
        MATCH (user:User)-[:SIMILARITY]->(otherUser:User)
        WHERE user.userId = $userId
        WITH otherUser, count(otherUser) as size
        MATCH (otherUser)-[r:BOOKMARKS]->(target:Item)
        WHERE target.itemId = $itemId
        return (+1.0/size)*count(r) as score
    """

    def __init__(self, argv):
        super().__init__(argv=argv)


class ItemRecommender(BaseRecommender):
    label = "User"
    property = "userId"
    sparse_vector_query = """
        MATCH (u:User )-[:BOOKMARKS]->(i:Item {itemId: $id})
        return id(u) as index, 1.0 as value
        order by index
    """
    score_query = """
        MATCH (user:User)-[:BOOKMARKS]->(item:Item)-[r:SIMILARITY]->(target:Item)
        WHERE user.userId = $userId AND target.itemId = $itemId
        return sum(r.value) as score
    """

    def __init__(self, argv):
        super().__init__(argv=argv)


class Recommender(GraphDBBase):
    class KNNType(Enum):
        USER = 1
        ITEM = 2

    def __init__(self, argv):
        super().__init__(command=__file__, argv=argv)
        self.strategies: Dict[Recommender.KNNType, BaseRecommender] = {
            Recommender.KNNType.USER: UserRecommender(argv),
            Recommender.KNNType.ITEM: ItemRecommender(argv)
        }

    def compute_and_store_KNN(self, type_: KNNType) -> None:
        strategy = self.strategies[type_]
        strategy.compute_and_store_KNN(20)

    def clean_KNN(self):
        print("cleaning previously computed KNNs")
        delete_query = "MATCH p=()-[r:SIMILARITY]->() DELETE r"
        with self._driver.session() as session:
            tx = session.begin_transaction()
            tx.run(delete_query)
            tx.commit()

    def get_recommendations(self, user_id: str, size: int, type_: KNNType):
        strategy = self.strategies[type_]
        return strategy.get_recommendations(user_id, size)


def main():
    # TODO: pass the user ID in the command-line
    recommender = Recommender(sys.argv[1:])
    recommender.clean_KNN()
    recommender.compute_and_store_KNN(recommender.KNNType.USER)
    user_id = "866"
    print(f"User-based recommendations for user {user_id}")
    recommendations = recommender.get_recommendations(user_id, 10, recommender.KNNType.USER)
    print(recommendations)
    recommender.clean_KNN()
    recommender.compute_and_store_KNN(recommender.KNNType.ITEM)
    user_id = "866"
    print(f"Item-based recommendations for user {user_id}")
    recommendations = recommender.get_recommendations(user_id, 10, recommender.KNNType.ITEM)
    print(recommendations)


if __name__ == '__main__':
    main()
